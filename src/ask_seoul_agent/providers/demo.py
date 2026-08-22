"""Deterministic keyless provider for local E2E tests and demos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ask_seoul_agent.models import ModelTurn, ToolCall, ToolResult, Usage
from ask_seoul_agent.providers.anthropic import fixed_tool_schemas
from ask_seoul_agent.providers.base import Message, ToolSchema
from ask_seoul_agent.weather_catalog import (
    is_weather_product_id,
    select_weather_product_id,
    weather_product_title,
)


class DemoProvider:
    """A deterministic provider that exercises the same tool loop without an LLM."""

    def tool_schemas(self) -> list[dict[str, Any]]:
        return fixed_tool_schemas()

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        *,
        timeout_s: float,
    ) -> ModelTurn:
        del tools, timeout_s
        tool_results = [_content(message) for message in messages if message.get("role") == "tool"]
        if not tool_results:
            question = _last_user_text(messages)
            return ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="demo_search_1",
                        name="search_products",
                        arguments={"query": question[:200] or "서울 기상 위험"},
                    )
                ],
                usage=Usage(input_tokens=0, output_tokens=0),
            )

        latest = tool_results[-1]
        if (
            isinstance(latest, ToolResult)
            and latest.tool == "search_products"
            and latest.status == "ok"
        ):
            candidates = latest.content.get("candidates", [])
            available_product_ids = {
                product_id
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and is_weather_product_id(product_id := candidate.get("product_id"))
            }
            product_id = select_weather_product_id(
                _last_user_text(messages),
                available_product_ids=available_product_ids,
            )
            if product_id is not None:
                return ModelTurn(
                    text=None,
                    tool_calls=[
                        ToolCall(
                            id="demo_preview_1",
                            name="preview_product",
                            arguments={"product_id": product_id},
                        )
                    ],
                    usage=Usage(input_tokens=0, output_tokens=0),
                )
            return ModelTurn(
                text=(
                    "현재 제공 중인 ASK Seoul 기상 제품 4개 범위에서 질문과 맞는 "
                    "제품을 찾지 못했습니다."
                ),
                tool_calls=[],
                usage=Usage(input_tokens=0, output_tokens=0),
            )

        if (
            isinstance(latest, ToolResult)
            and latest.tool == "preview_product"
            and latest.status == "ok"
        ):
            rows = latest.content.get("rows", [])
            product_id = latest.content.get("product_id", "unknown")
            summary = _summarize_rows(rows, product_id=product_id)
            return ModelTurn(
                text=(
                    "실제 LLM이 아닌 로컬 데모 모드입니다. "
                    f"{weather_product_title(str(product_id))}의 "
                    f"미리보기에서 {len(rows) if isinstance(rows, list) else 0}행을 확인했습니다. "
                    f"{summary}"
                ),
                tool_calls=[],
                usage=Usage(input_tokens=0, output_tokens=0),
            )

        return ModelTurn(
            text="도구 실행에서 답변에 사용할 수 있는 기상 미리보기 근거를 얻지 못했습니다.",
            tool_calls=[],
            usage=Usage(input_tokens=0, output_tokens=0),
        )


def _content(message: Mapping[str, Any]) -> Any:
    return message.get("content")


def _last_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
    return "서울 기상 위험"


def _summarize_rows(rows: Any, *, product_id: object) -> str:
    if not isinstance(rows, list) or not rows:
        return "반환된 미리보기 행이 없습니다."
    parts: list[str] = []
    for row in rows[:3]:
        if isinstance(row, Mapping):
            place = (
                row.get("place_name")
                or row.get("admin_dong")
                or row.get("place")
                or row.get("dong")
                or row.get("name")
                or "장소 미상"
            )
            if product_id == "weather_place_forecast_change_daily":
                raw_state = row.get("change_state")
                state = {
                    "unchanged": "변화 없음",
                    "changed": "변경됨",
                    "new": "새 예보",
                }.get(str(raw_state), "변화 상태 미상")
                forecast_date = row.get("forecast_date") or "날짜 미상"
                parts.append(f"{place}: {forecast_date} 예보 {state}")
            elif product_id == "weather_place_precipitation_window":
                start = row.get("window_start_at") or "시작 시각 미상"
                end = row.get("window_end_at") or "종료 시각 미상"
                probability = row.get("precip_prob_max_pct")
                probability_text = (
                    f", 최대 강수확률 {probability}%" if probability is not None else ""
                )
                parts.append(f"{place}: {start}~{end}{probability_text}")
            elif product_id == "weather_place_current_outlook":
                forecast_at = row.get("forecast_at") or "예보 시각 미상"
                temperature = row.get("temp_c")
                temperature_text = f", {temperature}℃" if temperature is not None else ""
                parts.append(f"{place}: {forecast_at}{temperature_text}")
            else:
                risk = (
                    row.get("risk_labels")
                    or row.get("risk")
                    or row.get("risk_level")
                    or "위험 정보 미상"
                )
                window = (
                    row.get("forecast_at")
                    or row.get("window")
                    or row.get("time_window")
                    or "시간대 미상"
                )
                parts.append(f"{place}: {window}, {risk}")
    return "샘플 요약:\n- " + "\n- ".join(parts) if parts else "미리보기 행을 확인했습니다."
