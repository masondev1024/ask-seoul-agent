"""Google Gemini GenerateContent adapter with manual, bounded function calling."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from google import genai
from google.genai import types
from pydantic import ValidationError

from ask_seoul_agent.models import ModelTurn, ToolCall, ToolResult, Usage
from ask_seoul_agent.providers.anthropic import (
    MAX_TOKENS,
    SYSTEM_PROMPT,
    fixed_tool_schemas,
)
from ask_seoul_agent.providers.base import Message, ProviderError, ToolSchema

DEFAULT_MODEL = "gemini-2.5-flash"
_THOUGHT_SIGNATURE = "gemini_thought_signature"
_PROVIDER_PARTS = "gemini_parts"
_SECRET_RE = re.compile(
    r"(AIza[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|(?:api[_-]?key)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)


class GeminiGenerateContentProvider:
    """Gemini SDK-backed provider that leaves tool execution to ``AgentRunner``."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._client = client or genai.Client(api_key=api_key)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return fixed_tool_schemas()

    async def aclose(self) -> None:
        async_client = getattr(self._client, "aio", None)
        async_close = getattr(async_client, "aclose", None)
        if callable(async_close):
            outcome = async_close()
            if inspect.isawaitable(outcome):
                await outcome
            return

        close = getattr(self._client, "close", None)
        if callable(close):
            outcome = close()
            if inspect.isawaitable(outcome):
                await outcome

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        *,
        timeout_s: float,
    ) -> ModelTurn:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=MAX_TOKENS,
            tools=_to_gemini_tools(tools),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(
                timeout=max(1, int(timeout_s * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=_to_gemini_contents(messages),
                config=config,
            )
        except Exception as exc:
            raise _provider_error(exc) from exc

        try:
            candidate = _first_candidate(response)
            parts = list(getattr(getattr(candidate, "content", None), "parts", None) or [])
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            provider_parts: list[dict[str, Any]] = []
            for part in parts:
                text = getattr(part, "text", None)
                function_call = getattr(part, "function_call", None)
                thought_signature = getattr(part, "thought_signature", None)
                signature = (
                    thought_signature
                    if isinstance(thought_signature, bytes) and thought_signature
                    else None
                )
                if function_call is None and isinstance(text, str):
                    is_thought = getattr(part, "thought", None) is True
                    provider_parts.append(
                        {
                            "kind": "text",
                            "text": text,
                            "thought": is_thought,
                            "thought_signature": signature,
                        }
                    )
                    if text and not is_thought:
                        text_parts.append(text)
                    continue
                if function_call is None:
                    if signature is not None:
                        raise ValueError("unsupported signed Gemini response part")
                    continue
                name = getattr(function_call, "name", None)
                arguments = getattr(function_call, "args", None)
                call_id = getattr(function_call, "id", None) or f"gemini-{uuid4().hex}"
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(call_id, str)
                    or not isinstance(arguments, Mapping)
                ):
                    raise ValueError("invalid Gemini function call")
                metadata: dict[str, Any] = {}
                if signature is not None:
                    metadata[_THOUGHT_SIGNATURE] = signature
                provider_parts.append(
                    {
                        "kind": "function_call",
                        "id": call_id,
                        "name": name,
                        "arguments": dict(arguments),
                        "thought_signature": signature,
                    }
                )
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=name,
                        arguments=dict(arguments),
                        provider_metadata=metadata,
                    )
                )

            stop_reason = _validated_finish_reason(candidate, has_tool_calls=bool(tool_calls))
            return ModelTurn(
                text="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls,
                usage=_usage_from_response(response),
                stop_reason=stop_reason,
                provider_metadata={_PROVIDER_PARTS: provider_parts},
            )
        except ProviderError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise ProviderError(
                "provider_protocol_error",
                "Gemini provider returned an invalid response.",
            ) from exc


def _to_gemini_tools(
    tools: Sequence[ToolSchema],
) -> list[types.Tool | Callable[..., Any]]:
    declarations: list[types.FunctionDeclaration] = []
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("input_schema")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(input_schema, Mapping)
        ):
            raise ProviderError(
                "provider_protocol_error",
                "Gemini provider received an invalid tool schema.",
            )
        declarations.append(
            types.FunctionDeclaration(
                name=name,
                description=description,
                parameters_json_schema=dict(input_schema),
            )
        )
    configured_tools: list[types.Tool | Callable[..., Any]] = []
    if declarations:
        configured_tools.append(types.Tool(function_declarations=declarations))
    return configured_tools


def _to_gemini_contents(messages: Sequence[Message]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            text = content if isinstance(content, str) else str(content)
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=text)])
            )
        elif role == "assistant" and isinstance(content, ModelTurn):
            contents.append(_assistant_content(content))
        elif role == "tool" and isinstance(content, ToolResult):
            contents.append(_tool_content(content))
    return contents


def _assistant_content(turn: ModelTurn) -> types.Content:
    recorded_parts = turn.provider_metadata.get(_PROVIDER_PARTS)
    if isinstance(recorded_parts, list) and recorded_parts:
        return types.Content(
            role="model",
            parts=[_replay_part(item) for item in recorded_parts],
        )

    parts: list[types.Part] = []
    if turn.text:
        parts.append(types.Part.from_text(text=turn.text))
    for call in turn.tool_calls:
        signature = call.provider_metadata.get(_THOUGHT_SIGNATURE)
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=call.id,
                    name=call.name,
                    args=call.arguments,
                ),
                thought_signature=signature if isinstance(signature, bytes) else None,
            )
        )
    return types.Content(role="model", parts=parts or [types.Part.from_text(text="")])


def _replay_part(value: Any) -> types.Part:
    if not isinstance(value, Mapping):
        raise ProviderError(
            "provider_protocol_error",
            "Gemini provider continuation state is invalid.",
        )
    signature = value.get("thought_signature")
    thought_signature = signature if isinstance(signature, bytes) else None
    if value.get("kind") == "text" and isinstance(value.get("text"), str):
        return types.Part(
            text=value["text"],
            thought=value.get("thought") is True,
            thought_signature=thought_signature,
        )
    if (
        value.get("kind") == "function_call"
        and isinstance(value.get("id"), str)
        and isinstance(value.get("name"), str)
        and isinstance(value.get("arguments"), Mapping)
    ):
        return types.Part(
            function_call=types.FunctionCall(
                id=value["id"],
                name=value["name"],
                args=dict(value["arguments"]),
            ),
            thought_signature=thought_signature,
        )
    raise ProviderError(
        "provider_protocol_error",
        "Gemini provider continuation state is invalid.",
    )


def _tool_content(result: ToolResult) -> types.Content:
    safe_payload = {
        "status": result.status,
        "content": result.content,
        "error": result.error,
        "source": result.source,
        "request_id": result.request_id,
    }
    return types.Content(
        role="tool",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=result.call_id,
                    name=result.tool,
                    response=safe_payload,
                )
            )
        ],
    )


def _first_candidate(response: Any) -> Any:
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, Sequence) or not candidates:
        raise ProviderError(
            "provider_protocol_error",
            "Gemini provider returned no response candidate.",
        )
    return candidates[0]


def _usage_from_response(response: Any) -> Usage:
    usage = getattr(response, "usage_metadata", None)
    input_tokens = _non_negative_int(getattr(usage, "prompt_token_count", 0))
    response_tokens = getattr(usage, "candidates_token_count", None)
    if response_tokens is None:
        response_tokens = getattr(usage, "response_token_count", 0)
    output_tokens = _non_negative_int(response_tokens)
    output_tokens += _non_negative_int(getattr(usage, "thoughts_token_count", 0))
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _validated_finish_reason(candidate: Any, *, has_tool_calls: bool) -> str:
    reason = _enum_value(getattr(candidate, "finish_reason", None))
    if reason == "STOP":
        return reason
    if reason == "MAX_TOKENS":
        raise ProviderError(
            "provider_incomplete",
            "Gemini provider response ended before completion.",
        )
    if reason in {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}:
        raise ProviderError(
            "provider_protocol_error",
            "Gemini provider returned an invalid function-call state.",
        )
    if reason in {
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }:
        raise ProviderError(
            "provider_refusal",
            "Gemini provider declined the request.",
        )
    if has_tool_calls and reason in {"FINISH_REASON_UNSPECIFIED", ""}:
        raise ProviderError(
            "provider_protocol_error",
            "Gemini provider returned an incomplete function-call state.",
        )
    raise ProviderError(
        "provider_protocol_error",
        "Gemini provider returned an unsupported finish reason.",
    )


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    if raw is None:
        return ""
    text = str(raw)
    return text.rsplit(".", 1)[-1].upper()


def _non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _provider_error(exc: Exception) -> ProviderError:
    status = (
        getattr(exc, "code", None)
        or getattr(exc, "status_code", None)
        or _status_from_text(str(exc))
    )
    if status in {401, 403}:
        return ProviderError(
            "provider_auth",
            f"Gemini provider rejected credentials with status {status}.",
        )
    if status == 429:
        return ProviderError(
            "provider_rate_limited",
            "Gemini provider rate limited the request.",
            retryable=True,
        )

    name = exc.__class__.__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return ProviderError(
            "provider_timeout",
            "Gemini provider request timed out.",
            retryable=True,
        )
    if "connection" in name or "network" in name or "connect" in name:
        return ProviderError(
            "provider_connection",
            "Gemini provider connection failed.",
            retryable=True,
        )
    if isinstance(status, int) and status >= 500:
        return ProviderError(
            "provider_unavailable",
            f"Gemini provider returned status {status}.",
            retryable=True,
        )

    # Never return raw SDK exception text; redaction is a defense-in-depth check only.
    _SECRET_RE.sub("[redacted]", str(exc))
    return ProviderError(
        "provider_error",
        "Gemini provider request failed.",
    )


def _status_from_text(message: str) -> int | None:
    match = re.search(r"(?<!\d)(401|403|429|5\d\d)(?!\d)", message)
    return int(match.group(1)) if match else None
