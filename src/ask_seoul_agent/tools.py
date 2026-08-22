"""Allowlisted ASK Seoul tools and request-scoped discovery state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import PRODUCT_ID_PATTERN, ToolResult


class AskSeoulPort(Protocol):
    async def search_products(self, query: str) -> list[dict[str, Any]]: ...

    async def preview_product(self, product_id: str) -> dict[str, Any]: ...


class SearchProductsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)


class PreviewProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(pattern=PRODUCT_ID_PATTERN)


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(slots=True)
class ToolContext:
    """Per-request state; never share discovery results across concurrent users."""

    discovered_products: dict[str, dict[str, Any]] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self, *, ask_seoul: AskSeoulPort) -> None:
        self._ask_seoul = ask_seoul

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_products",
                "description": (
                    "Search ASK Seoul public data products for the user's question. "
                    "This discovers candidates but is not evidence for a factual answer."
                ),
                "input_schema": SearchProductsInput.model_json_schema(),
            },
            {
                "name": "preview_product",
                "description": (
                    "Read the public five-row sample for a product returned by search_products "
                    "during this request. The result is sample-only evidence."
                ),
                "input_schema": PreviewProductInput.model_json_schema(),
            },
        ]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
        trace_id: str,
        call_id: str | None = None,
    ) -> ToolResult:
        del trace_id
        safe_call_id = call_id or f"tool_{name}"
        if name == "search_products":
            parsed = self._parse(SearchProductsInput, arguments)
            products = await self._ask_seoul.search_products(parsed.query.strip())
            bounded = [_bounded_mapping(product) for product in products[:3]]
            context.discovered_products.update(
                {
                    product_id: product
                    for product in bounded
                    if isinstance((product_id := product.get("product_id")), str)
                }
            )
            return ToolResult(
                call_id=safe_call_id,
                tool=name,
                status="ok",
                content={"candidates": bounded},
                source="https://ask-seoul.kr/api/v1/search",
            )

        if name == "preview_product":
            parsed = self._parse(PreviewProductInput, arguments)
            product = context.discovered_products.get(parsed.product_id)
            if product is None:
                raise ToolExecutionError(
                    "product_not_discovered",
                    "preview_product may only use a product returned by "
                    "search_products in this request",
                )
            payload = _bounded_mapping(await self._ask_seoul.preview_product(parsed.product_id))
            payload["product_id"] = parsed.product_id
            if not isinstance(payload.get("freshness"), str):
                freshness = product.get("freshness")
                if isinstance(freshness, str):
                    payload["freshness"] = freshness
            request_id = payload.get("request_id") or payload.get("_request_id")
            return ToolResult(
                call_id=safe_call_id,
                tool=name,
                status="ok",
                content=payload,
                source=f"https://ask-seoul.kr/api/v1/preview/{parsed.product_id}",
                request_id=request_id if isinstance(request_id, str) else None,
            )

        raise ToolExecutionError("unknown_tool", f"tool is not allowlisted: {name}")

    @staticmethod
    def _parse(model: type[BaseModel], arguments: dict[str, Any]) -> Any:
        try:
            return model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolExecutionError(
                "invalid_tool_input", "tool arguments failed validation"
            ) from exc


def _bounded_mapping(value: dict[str, Any]) -> dict[str, Any]:
    bounded = _bounded_value(value)
    return bounded if isinstance(bounded, dict) else {}


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:500]}…"
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key)[:100]: _bounded_value(item) for key, item in list(value.items())[:50]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]
