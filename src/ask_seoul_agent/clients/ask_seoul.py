from __future__ import annotations

import asyncio
import inspect
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from ask_seoul_agent.weather_catalog import (
    WEATHER_PRODUCT_BY_ID,
    WEATHER_PRODUCT_ID_SET,
    WEATHER_PRODUCT_IDS,
)

JsonObject = dict[str, Any]
Sleeper = Callable[[float], Awaitable[None]]
Jitter = Callable[[float], float]

_PRODUCT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_QUALITY_TOKENS = (
    "freshness",
    "stale",
    "quality",
    "publication",
    "product_not_ready",
    "품질",
    "발행",
)


class UpstreamError(Exception):
    """Sanitized ASK Seoul upstream failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id
        self.retry_count = retry_count


class UpstreamSchemaError(UpstreamError):
    """ASK Seoul returned a response that does not match the public contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "upstream_schema_error",
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retryable=retryable,
            status_code=status_code,
            request_id=request_id,
            retry_count=retry_count,
        )


class UpstreamQualityError(UpstreamError):
    """ASK Seoul refused the request because the data product quality gate failed."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "upstream_quality_error",
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retryable=retryable,
            status_code=status_code,
            request_id=request_id,
            retry_count=retry_count,
        )


class UpstreamTimeoutError(UpstreamError):
    """ASK Seoul did not respond within the configured timeout budget."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "upstream_timeout",
        retryable: bool = True,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retryable=retryable,
            status_code=status_code,
            request_id=request_id,
            retry_count=retry_count,
        )


class AskSeoulClient:
    """Small public REST client for ASK Seoul catalog search and previews."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 0.01,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Jitter | None = None,
    ) -> None:
        self._base_url = self._normalize_base_url(base_url)
        self._http = http_client
        self._max_retries = max(0, max_retries)
        self._retry_base_delay_seconds = max(0.0, retry_base_delay_seconds)
        self._sleeper = sleeper
        self._jitter = jitter or (lambda delay: delay + random.uniform(0.0, delay / 2))

    async def search_products(self, query: str) -> list[JsonObject]:
        self._validate_query(query)
        payload = await self._get_json(
            "/api/v1/catalog",
            params=None,
            operation="search_products",
        )
        products = payload.get("products")
        if not isinstance(products, list):
            raise UpstreamSchemaError(
                "ASK Seoul search response schema mismatch",
                code="upstream_schema_error",
                retryable=False,
            )

        by_id: dict[str, JsonObject] = {}
        for item in products:
            if not isinstance(item, dict):
                continue
            product_id = item.get("product_id")
            if not isinstance(product_id, str):
                continue
            if product_id not in WEATHER_PRODUCT_ID_SET:
                continue
            normalized = dict(item)
            product = WEATHER_PRODUCT_BY_ID[product_id]
            normalized.setdefault("title", product.title)
            normalized.setdefault("product_question", product.question)
            by_id[product_id] = normalized
        return [by_id[product_id] for product_id in WEATHER_PRODUCT_IDS if product_id in by_id]

    async def preview_product(self, product_id: str) -> JsonObject:
        safe_product_id = self._validate_product_id(product_id)
        payload = await self._get_json(
            f"/api/v1/preview/{quote(safe_product_id, safe='')}",
            params=None,
            operation="preview_product",
        )
        rows = payload.get("rows")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows[:5]):
            raise UpstreamSchemaError(
                "ASK Seoul preview response schema mismatch",
                code="upstream_schema_error",
                retryable=False,
            )
        preview = dict(payload)
        preview["rows"] = rows[:5]
        return preview

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None,
        operation: str,
    ) -> JsonObject:
        retry_count = 0
        last_status: int | None = None
        last_request_id: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.get(
                    self._url(path),
                    params=params,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    retry_count += 1
                    await self._sleep_before_retry(attempt)
                    continue
                raise UpstreamTimeoutError(
                    f"ASK Seoul {operation} timeout",
                    code="upstream_timeout",
                    retryable=True,
                    retry_count=retry_count,
                ) from exc
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    retry_count += 1
                    await self._sleep_before_retry(attempt)
                    continue
                raise UpstreamError(
                    f"ASK Seoul {operation} transport error",
                    code="upstream_transport_error",
                    retryable=True,
                    retry_count=retry_count,
                ) from exc

            last_status = response.status_code
            last_request_id = self._request_id(response)

            if 200 <= response.status_code < 300:
                payload = self._decode_json_object(
                    response,
                    operation=operation,
                    status_code=response.status_code,
                    request_id=last_request_id,
                )
                if last_request_id is not None:
                    payload.setdefault("request_id", last_request_id)
                return payload

            if response.status_code == 503 and self._is_quality_failure(response):
                raise UpstreamQualityError(
                    "요청한 ASK Seoul 기상 제품의 품질 또는 최신성 검증이 준비되지 않았습니다.",
                    code="upstream_quality_error",
                    retryable=False,
                    status_code=response.status_code,
                    request_id=last_request_id,
                    retry_count=retry_count,
                )

            if response.status_code in {429} or 500 <= response.status_code <= 599:
                if attempt < self._max_retries:
                    retry_count += 1
                    await self._sleep_before_retry(attempt, response=response)
                    continue
                raise UpstreamError(
                    f"ASK Seoul {operation} failed with retryable upstream status",
                    code="upstream_retryable_status",
                    retryable=True,
                    status_code=response.status_code,
                    request_id=last_request_id,
                    retry_count=retry_count,
                )

            raise UpstreamError(
                f"ASK Seoul {operation} failed with upstream status",
                code="upstream_status_error",
                retryable=False,
                status_code=response.status_code,
                request_id=last_request_id,
                retry_count=retry_count,
            )

        raise UpstreamError(
            f"ASK Seoul {operation} failed after retries",
            code="upstream_retry_exhausted",
            retryable=True,
            status_code=last_status,
            request_id=last_request_id,
            retry_count=retry_count,
        )

    def _decode_json_object(
        self,
        response: httpx.Response,
        *,
        operation: str,
        status_code: int,
        request_id: str | None,
    ) -> JsonObject:
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamSchemaError(
                f"ASK Seoul {operation} returned invalid JSON",
                code="upstream_schema_error",
                retryable=False,
                status_code=status_code,
                request_id=request_id,
            ) from exc
        if not isinstance(payload, dict):
            raise UpstreamSchemaError(
                f"ASK Seoul {operation} JSON root schema mismatch",
                code="upstream_schema_error",
                retryable=False,
                status_code=status_code,
                request_id=request_id,
            )
        return payload

    async def _sleep_before_retry(
        self,
        attempt: int,
        *,
        response: httpx.Response | None = None,
    ) -> None:
        retry_after = self._retry_after_seconds(response) if response is not None else None
        delay = (
            retry_after
            if retry_after is not None
            else self._retry_base_delay_seconds * (2**attempt)
        )
        maybe_awaitable = self._sleeper(self._jitter(max(0.0, delay)))
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    @staticmethod
    def _retry_after_seconds(response: httpx.Response | None) -> float | None:
        if response is None:
            return None
        value = response.headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _is_quality_failure(response: httpx.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        haystack = " ".join(str(value).lower() for value in payload.values())
        return any(token in haystack for token in _QUALITY_TOKENS)

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        for header in ("x-request-id", "cf-ray", "x-correlation-id"):
            value = response.headers.get(header)
            if value:
                return str(value[:128])
        return None

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        parsed = httpx.URL(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.host is None:
            raise ValueError("ASK Seoul base_url must be an absolute HTTP(S) URL")
        return str(parsed.copy_with(path="", query=None, fragment=None)).rstrip("/")

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = query.strip()
        if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
            raise ValueError("ASK Seoul search query must be 1-200 printable characters")
        return normalized

    @staticmethod
    def _validate_product_id(product_id: str) -> str:
        normalized = product_id.strip()
        if not _PRODUCT_ID_PATTERN.fullmatch(normalized):
            raise ValueError("ASK Seoul product_id contains invalid characters")
        if normalized not in WEATHER_PRODUCT_ID_SET:
            raise ValueError("현재 운영 중인 ASK Seoul 기상 제품 4개만 조회할 수 있습니다")
        return normalized
