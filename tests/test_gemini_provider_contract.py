from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class FakeAsyncModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Gemini called more times than scripted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeGeminiClient:
    def __init__(self, responses: list[Any]) -> None:
        self.aio = SimpleNamespace(models=FakeAsyncModels(responses))
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _part(
    *,
    text: str | None = None,
    function_call: Any = None,
    thought_signature: bytes | None = None,
    thought: bool | None = None,
) -> Any:
    return SimpleNamespace(
        text=text,
        function_call=function_call,
        thought_signature=thought_signature,
        thought=thought,
    )


def _response(
    parts: list[Any],
    *,
    finish_reason: str = "STOP",
    prompt_tokens: int = 11,
    output_tokens: int = 7,
) -> Any:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=parts),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
        ),
    )


def _read(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name)


def _config_value(config: Any, name: str) -> Any:
    if isinstance(config, dict):
        return config.get(name)
    return getattr(config, name)


def _first_function_declaration(config: Any) -> Any:
    tools = _config_value(config, "tools")
    declarations = _read(tools[0], "function_declarations")
    return declarations[0]


def _content_parts(content: Any) -> list[Any]:
    return list(_read(content, "parts"))


def test_gemini_declares_allowlisted_tools_with_function_declarations() -> None:
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    provider = GeminiGenerateContentProvider(api_key="test-key", client=FakeGeminiClient([]))
    schemas = provider.tool_schemas()

    assert {tool["name"] for tool in schemas} == {"search_products", "preview_product"}
    preview_schema = next(tool for tool in schemas if tool["name"] == "preview_product")
    assert (
        preview_schema["input_schema"]["properties"]["product_id"]["pattern"]
        == "^[A-Za-z][A-Za-z0-9_]{0,127}$"
    )


@pytest.mark.anyio
async def test_gemini_sends_system_instruction_and_converted_tools() -> None:
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    client = FakeGeminiClient([_response([_part(text="done")])])
    provider = GeminiGenerateContentProvider(api_key="test-key", client=client)

    turn = await provider.complete(
        messages=[{"role": "user", "content": "weather"}],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    assert turn.text == "done"
    sent = client.aio.models.calls[0]
    assert sent["model"] == provider.model
    config = sent["config"]
    system_instruction = _config_value(config, "system_instruction").lower()
    assert "untrusted data" in system_instruction
    assert "always call search_products" in system_instruction
    assert _config_value(config, "max_output_tokens") == 1024
    automatic = _config_value(config, "automatic_function_calling")
    assert _read(automatic, "disable") is True
    http_options = _config_value(config, "http_options")
    assert _read(http_options, "timeout") == 1000
    retry_options = _read(http_options, "retry_options")
    assert _read(retry_options, "attempts") == 1
    declaration = _first_function_declaration(config)
    assert _read(declaration, "name") == "search_products"
    parameters = _read(declaration, "parameters_json_schema") or _read(
        declaration, "parameters"
    )
    assert parameters["additionalProperties"] is False


@pytest.mark.anyio
async def test_gemini_parses_function_call_and_usage() -> None:
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    function_call = SimpleNamespace(
        id="gemini-call-1",
        name="search_products",
        args={"query": "weather"},
    )
    provider = GeminiGenerateContentProvider(
        api_key="test-key",
        client=FakeGeminiClient([_response([_part(function_call=function_call)])]),
    )

    turn = await provider.complete(
        messages=[{"role": "user", "content": "weather"}],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    assert turn.tool_calls[0].id == "gemini-call-1"
    assert turn.tool_calls[0].name == "search_products"
    assert turn.tool_calls[0].arguments == {"query": "weather"}
    assert turn.usage.input_tokens == 11
    assert turn.usage.output_tokens == 7


@pytest.mark.anyio
async def test_gemini_preserves_opaque_thought_signature_across_tool_round() -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    signature = b"opaque-provider-signature"
    function_call = SimpleNamespace(
        id="gemini-call-1",
        name="search_products",
        args={"query": "weather"},
    )
    client = FakeGeminiClient(
        [
            _response(
                [
                    _part(
                        function_call=function_call,
                        thought_signature=signature,
                    )
                ]
            ),
            _response([_part(text="done")]),
        ]
    )
    provider = GeminiGenerateContentProvider(api_key="test-key", client=client)

    first_turn = await provider.complete(
        messages=[{"role": "user", "content": "weather"}],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )
    await provider.complete(
        messages=[
            {"role": "user", "content": "weather"},
            {"role": "assistant", "content": first_turn},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="gemini-call-1",
                    tool="search_products",
                    status="ok",
                    content={"candidates": []},
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    model_content = client.aio.models.calls[1]["contents"][-2]
    sent_part = _content_parts(model_content)[0]
    assert _read(sent_part, "thought_signature") == signature
    assert "provider_metadata" not in first_turn.model_dump()


@pytest.mark.anyio
async def test_gemini_replays_signed_parts_in_order_without_exposing_thought_text() -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    thought_signature = b"signed-thought"
    call_signature = b"signed-call"
    function_call = SimpleNamespace(
        id="gemini-call-1",
        name="search_products",
        args={"query": "weather"},
    )
    client = FakeGeminiClient(
        [
            _response(
                [
                    _part(
                        text="private reasoning must not become answer text",
                        thought=True,
                        thought_signature=thought_signature,
                    ),
                    _part(
                        function_call=function_call,
                        thought_signature=call_signature,
                    ),
                ]
            ),
            _response([_part(text="done")]),
        ]
    )
    provider = GeminiGenerateContentProvider(api_key="test-key", client=client)

    first_turn = await provider.complete(
        messages=[{"role": "user", "content": "weather"}],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )
    assert first_turn.text is None

    await provider.complete(
        messages=[
            {"role": "user", "content": "weather"},
            {"role": "assistant", "content": first_turn},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="gemini-call-1",
                    tool="search_products",
                    status="ok",
                    content={"candidates": []},
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    replayed_parts = _content_parts(client.aio.models.calls[1]["contents"][-2])
    assert len(replayed_parts) == 2
    assert _read(replayed_parts[0], "text") == "private reasoning must not become answer text"
    assert _read(replayed_parts[0], "thought") is True
    assert _read(replayed_parts[0], "thought_signature") == thought_signature
    assert _read(replayed_parts[1], "thought_signature") == call_signature
    assert _read(_read(replayed_parts[1], "function_call"), "id") == "gemini-call-1"


@pytest.mark.anyio
async def test_gemini_places_tool_response_after_model_function_call() -> None:
    from ask_seoul_agent.models import ModelTurn, ToolCall, ToolResult
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    client = FakeGeminiClient([_response([_part(text="done")])])
    provider = GeminiGenerateContentProvider(api_key="test-key", client=client)

    await provider.complete(
        messages=[
            {"role": "user", "content": "weather"},
            {
                "role": "assistant",
                "content": ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="gemini-call-1",
                            name="search_products",
                            arguments={"query": "weather"},
                        )
                    ]
                ),
            },
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="gemini-call-1",
                    tool="search_products",
                    status="ok",
                    content={"candidates": []},
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    contents = client.aio.models.calls[0]["contents"]
    model_content = contents[-2]
    tool_content = contents[-1]
    assert _read(model_content, "role") == "model"
    sent_call = _read(_content_parts(model_content)[0], "function_call")
    assert _read(sent_call, "name") == "search_products"
    assert _read(sent_call, "id") == "gemini-call-1"
    assert _read(tool_content, "role") == "tool"
    sent_response = _read(_content_parts(tool_content)[0], "function_response")
    assert _read(sent_response, "name") == "search_products"
    assert _read(sent_response, "id") == "gemini-call-1"
    payload = _read(sent_response, "response")
    assert payload["status"] == "ok"
    assert payload["content"] == {"candidates": []}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("finish_reason", "expected_code"),
    [
        ("MAX_TOKENS", "provider_incomplete"),
        ("MALFORMED_FUNCTION_CALL", "provider_protocol_error"),
        ("UNEXPECTED_TOOL_CALL", "provider_protocol_error"),
        ("SAFETY", "provider_refusal"),
    ],
)
async def test_gemini_rejects_non_terminal_finish_reasons(
    finish_reason: str,
    expected_code: str,
) -> None:
    from ask_seoul_agent.providers.base import ProviderError
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    provider = GeminiGenerateContentProvider(
        api_key="test-key",
        client=FakeGeminiClient([_response([_part(text="partial")], finish_reason=finish_reason)]),
    )

    with pytest.raises(ProviderError) as error:
        await provider.complete(
            messages=[{"role": "user", "content": "weather"}],
            tools=provider.tool_schemas(),
            timeout_s=1,
        )

    assert error.value.code == expected_code
    assert "partial" not in str(error.value)


@pytest.mark.anyio
async def test_gemini_provider_errors_are_sanitized_and_retryable() -> None:
    from ask_seoul_agent.providers.base import ProviderError
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    provider = GeminiGenerateContentProvider(
        api_key="test-key",
        client=FakeGeminiClient([RuntimeError("429 key=AIza-secret-value")]),
    )

    with pytest.raises(ProviderError) as error:
        await provider.complete(
            messages=[{"role": "user", "content": "weather"}],
            tools=provider.tool_schemas(),
            timeout_s=1,
        )

    assert error.value.code == "provider_rate_limited"
    assert error.value.retryable is True
    assert "AIza-secret-value" not in str(error.value)


@pytest.mark.anyio
async def test_gemini_provider_closes_its_client() -> None:
    from ask_seoul_agent.providers.gemini import GeminiGenerateContentProvider

    client = FakeGeminiClient([])
    provider = GeminiGenerateContentProvider(api_key="test-key", client=client)

    await provider.aclose()

    assert client.close_calls == 1
