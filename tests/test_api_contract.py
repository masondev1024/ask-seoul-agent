import asyncio

import httpx
import pytest
from conftest import PRODUCT_ID, FakeAskSeoulClient, parse_sse_events
from fastapi.testclient import TestClient


def test_health_live_is_process_only() -> None:
    from ask_seoul_agent.api import create_app

    client = TestClient(create_app(provider_mode="demo"))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_responses_include_defensive_browser_headers() -> None:
    from ask_seoul_agent.api import create_app

    client = TestClient(create_app(provider_mode="demo"))

    response = client.get("/health/live")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_readiness_fails_for_anthropic_without_secret() -> None:
    from ask_seoul_agent.api import create_app

    client = TestClient(create_app(provider_mode="anthropic", anthropic_api_key=None))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readiness_fails_for_gemini_without_secret() -> None:
    from ask_seoul_agent.api import create_app

    client = TestClient(create_app(provider_mode="gemini", gemini_api_key=None))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_app_from_environment_reads_gemini_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ask_seoul_agent.api as api

    captured: dict[str, str] = {}

    class CapturingGeminiProvider:
        def __init__(self, *, api_key: str, model: str) -> None:
            captured.update(api_key=api_key, model=model)

        async def complete(self, messages, tools, *, timeout_s):
            raise AssertionError("provider should not be called by readiness")

    monkeypatch.setattr(api, "GeminiGenerateContentProvider", CapturingGeminiProvider)
    monkeypatch.setenv("AGENT_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "synthetic-test-model")

    client = TestClient(api.app_from_environment())

    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["provider_mode"] == "gemini"
    assert captured == {
        "api_key": "synthetic-test-key",
        "model": "synthetic-test-model",
    }


def test_chat_stream_rejects_invalid_question_with_422() -> None:
    from ask_seoul_agent.api import create_app

    client = TestClient(create_app(provider_mode="demo"))

    response = client.post("/api/v1/chat/stream", json={"question": ""})

    assert response.status_code == 422


def test_chat_stream_sse_events_are_ordered_and_traceable(
    product_candidate, preview_payload
) -> None:
    from ask_seoul_agent.api import create_app
    from ask_seoul_agent.providers.demo import DemoProvider

    app = create_app(
        provider_mode="demo",
        provider=DemoProvider(),
        ask_seoul=FakeAskSeoulClient(product=product_candidate, preview=preview_payload),
    )
    client = TestClient(app)

    with client.stream(
        "POST", "/api/v1/chat/stream", json={"question": "weather risk preview"}
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    events = parse_sse_events(body)
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
    trace_ids = {event["trace_id"] for event in events}
    assert len(trace_ids) == 1
    final = next(event for event in events if event["event"] == "assistant.final")
    assert final["envelope"]["evidence_status"] == "grounded_preview"
    assert final["envelope"]["evidence"][0]["product_id"] == PRODUCT_ID

    preview_event = next(
        event
        for event in events
        if event["event"] == "tool.result" and event["tool_name"] == "preview_product"
    )
    assert "rows" not in preview_event["output"]
    assert "result" not in preview_event
    assert "answer" not in final
    assert "evidence" not in final
    assert "tool_results" not in final["envelope"]


def test_chat_stream_stale_upstream_returns_failure_without_hallucinated_answer(
    product_candidate,
) -> None:
    from ask_seoul_agent.api import create_app
    from ask_seoul_agent.clients.ask_seoul import UpstreamQualityError
    from ask_seoul_agent.providers.demo import DemoProvider

    app = create_app(
        provider_mode="demo",
        provider=DemoProvider(),
        ask_seoul=FakeAskSeoulClient(
            product=product_candidate,
            preview_error=UpstreamQualityError("publication freshness gate failed"),
        ),
    )
    client = TestClient(app)

    with client.stream(
        "POST", "/api/v1/chat/stream", json={"question": "weather risk preview"}
    ) as response:
        body = response.read().decode()

    events = parse_sse_events(body)
    assert response.status_code == 200
    assert any(
        event["event"] == "session.error" and event["error"]["code"] == "upstream_quality"
        for event in events
    )
    final = next(event for event in events if event["event"] == "assistant.final")
    assert final["envelope"]["evidence_status"] == "insufficient_data"
    assert "high risk" not in final["envelope"]["answer"].lower()


@pytest.mark.anyio
async def test_chat_stream_rejects_work_above_concurrency_budget() -> None:
    from ask_seoul_agent.api import create_app
    from ask_seoul_agent.models import ModelTurn

    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider:
        async def complete(self, messages, tools, *, timeout_s):
            started.set()
            await release.wait()
            return ModelTurn(text="No tool evidence", tool_calls=[])

    app = create_app(
        provider_mode="demo",
        provider=BlockingProvider(),
        ask_seoul=FakeAskSeoulClient(),
        max_concurrent=1,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(
            client.post("/api/v1/chat/stream", json={"question": "first request"})
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        second = await client.post(
            "/api/v1/chat/stream",
            json={"question": "second request"},
        )
        release.set()
        first_response = await asyncio.wait_for(first, timeout=1)

    assert second.status_code == 429
    assert "concurrency" in second.json()["detail"].lower()
    assert first_response.status_code == 200
