import os
from pathlib import Path

import pytest
from conftest import parse_sse_events
from fastapi.testclient import TestClient

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.v1.json"


def _live_secret(flag: str) -> str:
    if os.getenv(flag) != "1":
        pytest.skip(f"set {flag}=1 to explicitly authorize paid live evaluation")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is not configured in the local environment")
    return api_key


@pytest.mark.live
@pytest.mark.anyio
async def test_anthropic_live_eval_uses_recorded_tool_fixtures() -> None:
    """Paid, variable model evaluation; intentionally excluded from the default test lane."""
    from ask_seoul_agent.evaluation import load_eval_manifest
    from ask_seoul_agent.live_eval import run_live_suite
    from ask_seoul_agent.providers.anthropic import DEFAULT_MODEL, AnthropicMessagesProvider

    api_key = _live_secret("AGENT_EVAL_LIVE")
    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    provider = AnthropicMessagesProvider(api_key=api_key, model=model)
    try:
        report = await run_live_suite(
            load_eval_manifest(MANIFEST_PATH),
            provider=provider,
            provider_name="anthropic",
            model=model,
            application_version="0.1.0",
        )
    finally:
        await provider.aclose()

    assert report.summary.total == 10
    assert report.summary.inconclusive_infra == 0
    assert report.summary.passed == 10
    assert all(result.scores.safety == 1 for result in report.results)
    assert sum(result.observed.input_tokens for result in report.results) > 0
    assert sum(result.observed.output_tokens for result in report.results) > 0


@pytest.mark.live
def test_anthropic_and_ask_seoul_live_sse_e2e() -> None:
    """One paid model request flow plus live ASK Seoul REST, through the public SSE API."""
    from ask_seoul_agent.api import create_app
    from ask_seoul_agent.providers.anthropic import DEFAULT_MODEL

    api_key = _live_secret("AGENT_LIVE_E2E")
    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    app = create_app(
        provider_mode="anthropic",
        anthropic_api_key=api_key,
        anthropic_model=model,
        max_concurrent=1,
    )

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={
                "question": (
                    "ASK Seoul에서 장소별 기상 위험 예상 시간대 상품을 검색한 뒤 "
                    "공개 미리보기 샘플만 근거로 설명해줘"
                )
            },
        ) as response:
            body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert response.headers["x-trace-id"]
    events = parse_sse_events(body)
    tool_path = [
        event["tool_name"] for event in events if event["event"] == "tool.call"
    ]
    final = next(event for event in events if event["event"] == "assistant.final")
    done = next(event for event in events if event["event"] == "session.done")

    assert tool_path == ["search_products", "preview_product"]
    assert final["envelope"]["provider"] == "anthropic"
    assert final["envelope"]["evidence_status"] == "grounded_preview"
    assert final["envelope"]["evidence"][0]["sample_only"] is True
    assert final["envelope"]["usage"]["input_tokens"] > 0
    assert done["status"] == "completed"
