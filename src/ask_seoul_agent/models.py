"""Provider-neutral domain and API contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .weather_catalog import WeatherProductId, is_weather_product_id

PRODUCT_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
_PRODUCT_ID_RE = re.compile(PRODUCT_ID_PATTERN)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class StrictModel(BaseModel):
    """Base model that rejects contract drift at trust boundaries."""

    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if _CONTROL_RE.search(value):
            raise ValueError("question contains forbidden control characters")
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Provider-private continuation data (for example, Gemini thought signatures).
    # It is carried only inside the in-memory tool loop and never serialized to API events.
    provider_metadata: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_known_argument_shape(self) -> Self:
        if self.name == "preview_product":
            product_id = self.arguments.get("product_id")
            if not is_weather_product_id(product_id):
                raise ValueError("preview_product requires an allowed weather product_id")
        elif self.name == "search_products":
            query = self.arguments.get("query")
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
                raise ValueError("search_products requires a 1-200 character query")
        return self


class ToolResult(StrictModel):
    call_id: str = Field(min_length=1, max_length=200)
    tool: str = Field(min_length=1, max_length=100)
    status: Literal["ok", "error"]
    content: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    request_id: str | None = None
    error: dict[str, Any] | None = None


class ModelTurn(StrictModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str | None = None
    # Opaque provider continuation state is in-memory only and never part of API/eval output.
    provider_metadata: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class EvidenceStatus(StrEnum):
    GROUNDED_PREVIEW = "grounded_preview"
    INSUFFICIENT_DATA = "insufficient_data"


class Evidence(StrictModel):
    tool: Literal["preview_product"] = "preview_product"
    product_id: WeatherProductId
    source: str
    request_id: str | None = None
    freshness: str | None = None
    row_count: int = Field(ge=0, le=5)
    sample_only: Literal[True] = True
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=5)


_NO_EVIDENCE_ANSWER = (
    "근거 데이터를 확보하지 못해 답변을 생성하지 않았습니다. "
    "질문을 더 구체적으로 바꾸거나 ASK Seoul 기상 제품 상태를 확인해 주세요."
)
_PREVIEW_DISCLAIMER = (
    "주의: 아래 답변은 ASK Seoul의 공개 5행 미리보기 샘플만 근거로 하며, "
    "전체 데이터나 최신 상태를 보장하지 않습니다."
)


class FinalEnvelope(StrictModel):
    trace_id: str
    provider: str
    answer: str
    evidence_status: EvidenceStatus
    evidence: list[Evidence] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    elapsed_ms: int = Field(ge=0)

    @classmethod
    def from_agent_state(
        cls,
        *,
        trace_id: str,
        provider: str,
        answer_from_model: str | None,
        tool_results: list[ToolResult],
        usage: Usage | None,
        elapsed_ms: int,
    ) -> FinalEnvelope:
        evidence = [item for result in tool_results if (item := _evidence_from(result))]
        if not evidence:
            return cls(
                trace_id=trace_id,
                provider=provider,
                answer=_NO_EVIDENCE_ANSWER,
                evidence_status=EvidenceStatus.INSUFFICIENT_DATA,
                evidence=[],
                usage=usage or Usage(),
                elapsed_ms=elapsed_ms,
            )

        model_answer = (answer_from_model or "").strip()
        if not model_answer:
            model_answer = "미리보기 데이터는 근거 패널에서 직접 확인할 수 있습니다."
        return cls(
            trace_id=trace_id,
            provider=provider,
            answer=f"{_PREVIEW_DISCLAIMER}\n\n{model_answer}",
            evidence_status=EvidenceStatus.GROUNDED_PREVIEW,
            evidence=evidence,
            usage=usage or Usage(),
            elapsed_ms=elapsed_ms,
        )


def _evidence_from(result: ToolResult) -> Evidence | None:
    if result.status != "ok" or result.tool != "preview_product" or not result.source:
        return None
    product_id = result.content.get("product_id")
    rows = result.content.get("rows")
    if not is_weather_product_id(product_id):
        return None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    bounded_rows = [dict(row) for row in rows[:5]]
    if not bounded_rows:
        return None
    freshness = result.content.get("freshness")
    return Evidence(
        product_id=product_id,
        source=result.source,
        request_id=result.request_id,
        freshness=freshness if isinstance(freshness, str) else None,
        row_count=len(bounded_rows),
        rows=bounded_rows,
    )
