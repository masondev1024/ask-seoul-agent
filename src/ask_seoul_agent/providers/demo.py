"""Deterministic keyless provider for local E2E tests and demos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ask_seoul_agent.models import ModelTurn, ToolCall, ToolResult, Usage
from ask_seoul_agent.providers.anthropic import fixed_tool_schemas
from ask_seoul_agent.providers.base import Message, ToolSchema


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
                        arguments={"query": question[:200] or "weather risk"},
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
            if candidates:
                product_id = candidates[0].get("product_id")
                if isinstance(product_id, str):
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
                    "Demo provider: ASK Seoul search returned no candidate products, "
                    "so no grounded answer can be produced."
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
            summary = _summarize_rows(rows)
            return ModelTurn(
                text=(
                    "Demo provider, not a live LLM: "
                    f"preview sample for {product_id} contains "
                    f"{len(rows) if isinstance(rows, list) else 0} rows. "
                    f"{summary}"
                ),
                tool_calls=[],
                usage=Usage(input_tokens=0, output_tokens=0),
            )

        return ModelTurn(
            text="Demo provider: tool execution did not return usable preview evidence.",
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
    return "weather risk"


def _summarize_rows(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return "No preview rows were returned."
    parts: list[str] = []
    for row in rows[:3]:
        if isinstance(row, Mapping):
            place = (
                row.get("place_name")
                or row.get("admin_dong")
                or row.get("place")
                or row.get("dong")
                or row.get("name")
                or "unknown place"
            )
            risk = (
                row.get("risk_labels") or row.get("risk") or row.get("risk_level") or "unknown risk"
            )
            window = (
                row.get("forecast_at")
                or row.get("window")
                or row.get("time_window")
                or "unknown window"
            )
            parts.append(f"{place}: {risk} during {window}")
    return "Sample rows: " + "; ".join(parts) if parts else "Preview rows were returned."
