"""Bounded, evidence-first tool loop."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from .clients.ask_seoul import UpstreamError
from .models import FinalEnvelope, ToolResult, Usage
from .providers.base import LLMProvider, ProviderError
from .tools import ToolContext, ToolExecutionError, ToolRegistry

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: ToolRegistry,
        provider_name: str,
        max_rounds: int = 4,
        max_tool_calls: int = 4,
        total_timeout_s: float = 45.0,
        provider_timeout_s: float = 30.0,
    ) -> None:
        if max_rounds < 1 or max_tool_calls < 1:
            raise ValueError("agent budgets must be positive")
        self._provider = provider
        self._tools = tools
        self._provider_name = provider_name
        self._max_rounds = max_rounds
        self._max_tool_calls = max_tool_calls
        self._total_timeout_s = total_timeout_s
        self._provider_timeout_s = provider_timeout_s

    async def stream(self, question: str, *, trace_id: str) -> AsyncIterator[dict[str, Any]]:
        started = time.monotonic()
        deadline = started + self._total_timeout_s
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        context = ToolContext()
        tool_results: list[ToolResult] = []
        usage = Usage()
        answer_from_model: str | None = None
        tool_count = 0
        terminal_error = False

        yield self._event(
            "session.start",
            trace_id,
            provider_mode=self._provider_name,
            status="started",
        )

        for round_index in range(self._max_rounds):
            yield self._event(
                "assistant.status",
                trace_id,
                status="requesting_model_turn",
                round=round_index + 1,
            )
            try:
                remaining = self._remaining(deadline)
                available_tool_schemas = self._available_tool_schemas(tool_results)
                allowed_tool_names = {
                    name
                    for schema in available_tool_schemas
                    if isinstance((name := schema.get("name")), str)
                }
                async with asyncio.timeout(remaining):
                    turn = await self._provider.complete(
                        messages,
                        available_tool_schemas,
                        timeout_s=min(self._provider_timeout_s, remaining),
                    )
            except ProviderError as exc:
                terminal_error = True
                yield self._error_event(trace_id, exc.code, str(exc), retryable=exc.retryable)
                break
            except TimeoutError:
                terminal_error = True
                yield self._error_event(
                    trace_id,
                    "agent_timeout",
                    "The agent request exceeded its deadline.",
                    retryable=True,
                )
                break
            except Exception as exc:
                terminal_error = True
                logger.error(
                    "agent_unexpected_provider_error",
                    extra={
                        "trace_id": trace_id,
                        "provider": self._provider_name,
                        "exception_type": type(exc).__name__,
                    },
                )
                yield self._error_event(
                    trace_id,
                    "agent_internal_error",
                    "The agent could not safely process the provider response.",
                )
                break

            usage = usage + turn.usage
            if any(call.name not in allowed_tool_names for call in turn.tool_calls):
                terminal_error = True
                yield self._error_event(
                    trace_id,
                    "provider_protocol_error",
                    "The provider requested a tool that is unavailable in the current stage.",
                )
                break
            if not turn.tool_calls:
                answer_from_model = turn.text
                break

            messages.append({"role": "assistant", "content": turn})
            current_results: list[ToolResult] = []
            for call in turn.tool_calls:
                if tool_count >= self._max_tool_calls:
                    terminal_error = True
                    yield self._error_event(
                        trace_id,
                        "tool_budget_exceeded",
                        "The agent reached the configured tool-call budget.",
                    )
                    break

                tool_count += 1
                yield self._event(
                    "tool.call",
                    trace_id,
                    tool_name=call.name,
                    tool_call_id=call.id,
                    input=call.arguments,
                )
                try:
                    remaining = self._remaining(deadline)
                    async with asyncio.timeout(remaining):
                        result = await self._tools.execute(
                            call.name,
                            call.arguments,
                            context=context,
                            trace_id=trace_id,
                            call_id=call.id,
                        )
                except UpstreamError as exc:
                    result = self._failed_result(
                        call.id,
                        call.name,
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                    )
                    yield self._error_event(
                        trace_id,
                        _public_upstream_code(exc),
                        str(exc),
                        retryable=exc.retryable,
                    )
                except ToolExecutionError as exc:
                    result = self._failed_result(
                        call.id,
                        call.name,
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                    )
                    yield self._error_event(
                        trace_id,
                        exc.code,
                        str(exc),
                        retryable=exc.retryable,
                    )
                except TimeoutError:
                    result = self._failed_result(
                        call.id,
                        call.name,
                        code="agent_timeout",
                        message="The agent request exceeded its deadline.",
                        retryable=True,
                    )
                    terminal_error = True
                    yield self._error_event(
                        trace_id,
                        "agent_timeout",
                        "The agent request exceeded its deadline.",
                        retryable=True,
                    )
                except Exception as exc:
                    result = self._failed_result(
                        call.id,
                        call.name,
                        code="tool_internal_error",
                        message="The tool failed unexpectedly.",
                        retryable=False,
                    )
                    terminal_error = True
                    logger.error(
                        "agent_unexpected_tool_error",
                        extra={
                            "trace_id": trace_id,
                            "provider": self._provider_name,
                            "tool_name": call.name,
                            "tool_call_id": call.id,
                            "exception_type": type(exc).__name__,
                        },
                    )
                    yield self._error_event(
                        trace_id,
                        "tool_internal_error",
                        "The tool failed unexpectedly.",
                    )

                current_results.append(result)
                tool_results.append(result)
                yield self._event(
                    "tool.result",
                    trace_id,
                    tool_name=call.name,
                    tool_call_id=call.id,
                    ok=result.status == "ok",
                    output=_public_tool_output(result),
                )
                if terminal_error:
                    break

            messages.extend({"role": "tool", "content": result} for result in current_results)
            if terminal_error:
                break
        else:
            terminal_error = True
            yield self._error_event(
                trace_id,
                "round_budget_exceeded",
                "The agent reached the configured model-round budget.",
            )

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        envelope = FinalEnvelope.from_agent_state(
            trace_id=trace_id,
            provider=self._provider_name,
            answer_from_model=answer_from_model,
            tool_results=tool_results,
            usage=usage,
            elapsed_ms=elapsed_ms,
        )
        yield self._event(
            "assistant.final",
            trace_id,
            envelope=envelope,
        )
        yield self._event(
            "session.done",
            trace_id,
            status="completed_with_error" if terminal_error else "completed",
            evidence_status=envelope.evidence_status,
            elapsed_ms=elapsed_ms,
        )
        logger.info(
            "agent_completed",
            extra={
                "trace_id": trace_id,
                "provider": self._provider_name,
                "elapsed_ms": elapsed_ms,
                "tool_count": tool_count,
                "status": "completed_with_error" if terminal_error else "completed",
                "evidence_status": envelope.evidence_status.value,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _available_tool_schemas(
        self,
        tool_results: list[ToolResult],
    ) -> list[dict[str, Any]]:
        schemas = self._tools.schemas()
        if not tool_results:
            allowed_name = "search_products"
        else:
            latest = tool_results[-1]
            candidates = latest.content.get("candidates")
            if (
                latest.tool == "search_products"
                and latest.status == "ok"
                and isinstance(candidates, list)
                and bool(candidates)
            ):
                allowed_name = "preview_product"
            else:
                return []
        return [schema for schema in schemas if schema.get("name") == allowed_name]

    @staticmethod
    def _event(event: str, trace_id: str, **payload: Any) -> dict[str, Any]:
        return {"event": event, "trace_id": trace_id, **payload}

    @classmethod
    def _error_event(
        cls,
        trace_id: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        safe_message = message[:300]
        return cls._event(
            "session.error",
            trace_id,
            error={"code": code, "message": safe_message, "retryable": retryable},
            message=safe_message,
        )

    @staticmethod
    def _failed_result(
        call_id: str,
        tool: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> ToolResult:
        return ToolResult(
            call_id=call_id,
            tool=tool,
            status="error",
            content={},
            error={"code": code, "message": message[:300], "retryable": retryable},
        )


def _public_upstream_code(exc: UpstreamError) -> str:
    if exc.code == "upstream_quality_error":
        return "upstream_quality"
    return exc.code


def _public_tool_output(result: ToolResult) -> dict[str, Any]:
    if result.tool == "preview_product" and result.status == "ok":
        rows = result.content.get("rows")
        return {
            "product_id": result.content.get("product_id"),
            "freshness": result.content.get("freshness"),
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "sample_only": True,
        }
    return result.content
