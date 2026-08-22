import asyncio

import pytest
from conftest import PRODUCT_ID, FakeAskSeoulClient


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.messages = []

    async def complete(self, messages, tools, *, timeout_s):
        self.messages.append(messages)
        if not self.turns:
            raise AssertionError("provider called more times than scripted")
        return self.turns.pop(0)


@pytest.mark.anyio
async def test_demo_two_tool_happy_path_returns_preview_grounded_final(
    product_candidate, preview_payload
) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus, ModelTurn, ToolCall, Usage
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider(
        [
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call_search",
                        name="search_products",
                        arguments={"query": "weather risk"},
                    )
                ],
                usage=Usage(input_tokens=10, output_tokens=5),
            ),
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call_preview",
                        name="preview_product",
                        arguments={"product_id": PRODUCT_ID},
                    )
                ],
                usage=Usage(input_tokens=20, output_tokens=5),
            ),
            ModelTurn(
                text="Gangnam has high risk in the preview sample.",
                tool_calls=[],
                usage=Usage(input_tokens=30, output_tokens=10),
            ),
        ]
    )
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(
            ask_seoul=FakeAskSeoulClient(product=product_candidate, preview=preview_payload)
        ),
        provider_name="demo",
        max_rounds=4,
        max_tool_calls=4,
    )

    events = [event async for event in runner.stream("show weather risk", trace_id="trace-1")]

    assert [event["event"] for event in events] == [
        "session.start",
        "assistant.status",
        "tool.call",
        "tool.result",
        "assistant.status",
        "tool.call",
        "tool.result",
        "assistant.status",
        "assistant.final",
        "session.done",
    ]
    final = next(event for event in events if event["event"] == "assistant.final")
    assert final["envelope"].evidence_status == EvidenceStatus.GROUNDED_PREVIEW
    assert final["trace_id"] == "trace-1"


@pytest.mark.anyio
async def test_no_evidence_final_replaces_provider_prose_with_failure_response() -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus, ModelTurn
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider([ModelTurn(text="Unsupported answer", tool_calls=[])])
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(ask_seoul=FakeAskSeoulClient()),
        provider_name="demo",
        max_rounds=4,
        max_tool_calls=4,
    )

    events = [event async for event in runner.stream("show weather risk", trace_id="trace-1")]

    final = next(event for event in events if event["event"] == "assistant.final")
    assert final["envelope"].evidence_status == EvidenceStatus.INSUFFICIENT_DATA
    assert "Unsupported answer" not in final["envelope"].answer


@pytest.mark.anyio
async def test_runner_stops_when_max_tool_calls_is_reached(product_candidate) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import ModelTurn, ToolCall
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider(
        [
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="call_1", name="search_products", arguments={"query": "weather"})
                ],
            ),
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="call_2", name="search_products", arguments={"query": "risk"})
                ],
            ),
        ]
    )
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(ask_seoul=FakeAskSeoulClient(product=product_candidate)),
        provider_name="demo",
        max_rounds=4,
        max_tool_calls=1,
    )

    events = [event async for event in runner.stream("show weather risk", trace_id="trace-1")]

    assert any(
        event["event"] == "session.error" and event["error"]["code"] == "tool_budget_exceeded"
        for event in events
    )


@pytest.mark.anyio
async def test_runner_stops_when_max_rounds_is_reached(product_candidate) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import ModelTurn, ToolCall
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider(
        [
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="call_1", name="search_products", arguments={"query": "weather"})
                ],
            ),
            ModelTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="call_2", name="search_products", arguments={"query": "risk"})
                ],
            ),
        ]
    )
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(ask_seoul=FakeAskSeoulClient(product=product_candidate)),
        provider_name="demo",
        max_rounds=1,
        max_tool_calls=4,
    )

    events = [event async for event in runner.stream("show weather risk", trace_id="trace-1")]

    assert any(
        event["event"] == "session.error" and event["error"]["code"] == "round_budget_exceeded"
        for event in events
    )


@pytest.mark.anyio
async def test_demo_provider_summarizes_live_preview_field_names(preview_payload) -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.demo import DemoProvider

    live_shape = dict(preview_payload)
    live_shape["rows"] = [
        {
            "place_name": "청운효자동",
            "risk_labels": "폭염후보",
            "forecast_at": "2026-08-23 15:00:00",
        }
    ]
    provider = DemoProvider()
    turn = await provider.complete(
        messages=[
            {"role": "user", "content": "기상 위험"},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="preview_1",
                    tool="preview_product",
                    status="ok",
                    content=live_shape,
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    assert "청운효자동" in (turn.text or "")
    assert "폭염후보" in (turn.text or "")
    assert "2026-08-23 15:00:00" in (turn.text or "")


@pytest.mark.anyio
async def test_runner_fails_closed_on_unexpected_provider_exception() -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus
    from ask_seoul_agent.tools import ToolRegistry

    class ExplodingProvider:
        async def complete(self, messages, tools, *, timeout_s):
            raise RuntimeError("secret-token-must-not-leak")

    runner = AgentRunner(
        provider=ExplodingProvider(),
        tools=ToolRegistry(ask_seoul=FakeAskSeoulClient()),
        provider_name="demo",
    )

    events = [event async for event in runner.stream("weather", trace_id="trace-1")]

    error = next(event for event in events if event["event"] == "session.error")
    final = next(event for event in events if event["event"] == "assistant.final")
    assert error["error"]["code"] == "agent_internal_error"
    assert "secret-token-must-not-leak" not in str(events)
    assert final["envelope"].evidence_status == EvidenceStatus.INSUFFICIENT_DATA


@pytest.mark.anyio
async def test_runner_enforces_total_deadline_and_emits_terminal_events() -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus, ModelTurn
    from ask_seoul_agent.tools import ToolRegistry

    class SlowProvider:
        async def complete(self, messages, tools, *, timeout_s):
            await asyncio.sleep(1)
            return ModelTurn(text="late unsupported answer")

    runner = AgentRunner(
        provider=SlowProvider(),
        tools=ToolRegistry(ask_seoul=FakeAskSeoulClient()),
        provider_name="demo",
        total_timeout_s=0.01,
        provider_timeout_s=1,
    )

    events = [event async for event in runner.stream("weather", trace_id="trace-1")]

    assert any(
        event["event"] == "session.error" and event["error"]["code"] == "agent_timeout"
        for event in events
    )
    final = next(event for event in events if event["event"] == "assistant.final")
    done = next(event for event in events if event["event"] == "session.done")
    assert final["envelope"].evidence_status == EvidenceStatus.INSUFFICIENT_DATA
    assert done["status"] == "completed_with_error"


@pytest.mark.anyio
async def test_unexpected_tool_failure_emits_only_internal_error(product_candidate) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import ModelTurn, ToolCall
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call_search",
                        name="search_products",
                        arguments={"query": "weather"},
                    )
                ]
            )
        ]
    )
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(
            ask_seoul=FakeAskSeoulClient(
                product=product_candidate,
                search_error=RuntimeError("unexpected secret detail"),
            )
        ),
        provider_name="demo",
    )

    events = [event async for event in runner.stream("weather", trace_id="trace-1")]
    errors = [event["error"]["code"] for event in events if event["event"] == "session.error"]

    assert errors == ["tool_internal_error"]
    assert "unexpected secret detail" not in str(events)


@pytest.mark.anyio
async def test_tool_deadline_emits_only_timeout_error(product_candidate) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import ModelTurn, ToolCall
    from ask_seoul_agent.tools import ToolRegistry

    provider = ScriptedProvider(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call_search",
                        name="search_products",
                        arguments={"query": "weather"},
                    )
                ]
            )
        ]
    )
    runner = AgentRunner(
        provider=provider,
        tools=ToolRegistry(
            ask_seoul=FakeAskSeoulClient(
                product=product_candidate,
                search_error=TimeoutError("tool timeout detail"),
            )
        ),
        provider_name="demo",
    )

    events = [event async for event in runner.stream("weather", trace_id="trace-1")]
    errors = [event["error"]["code"] for event in events if event["event"] == "session.error"]

    assert errors == ["agent_timeout"]
    done = next(event for event in events if event["event"] == "session.done")
    assert done["status"] == "completed_with_error"
