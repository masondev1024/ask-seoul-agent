from collections.abc import Sequence
from pathlib import Path

import pytest

from ask_seoul_agent.models import ModelTurn
from ask_seoul_agent.providers.base import Message, ToolSchema

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.v1.json"


class ProviderMustNotBeCalled:
    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        *,
        timeout_s: float,
    ) -> ModelTurn:
        raise AssertionError("provider must not be called for invalid live case selection")


@pytest.mark.anyio
async def test_live_lane_runs_only_applicable_cases_against_recorded_tool_fixtures() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest
    from ask_seoul_agent.live_eval import run_live_suite
    from ask_seoul_agent.providers.demo import DemoProvider

    manifest = load_eval_manifest(MANIFEST_PATH)
    expected_ids = {case.case_id for case in manifest.cases if "live" in case.run_modes}

    report = await run_live_suite(
        manifest,
        provider=DemoProvider(),
        provider_name="anthropic-contract",
        model="fixture-model",
        application_version="0.1.0",
    )

    assert len(expected_ids) == 10
    assert report.mode == "live"
    assert report.provider.name == "anthropic-contract"
    assert report.provider.model == "fixture-model"
    assert report.summary.total == 10
    assert report.summary.passed == 10
    assert report.summary.failed == 0
    assert {result.case_id for result in report.results} == expected_ids


@pytest.mark.anyio
async def test_live_lane_classifies_provider_infrastructure_failure_as_inconclusive() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest
    from ask_seoul_agent.live_eval import run_live_suite
    from ask_seoul_agent.providers.base import ProviderError

    class RateLimitedProvider:
        async def complete(
            self,
            messages: Sequence[Message],
            tools: Sequence[ToolSchema],
            *,
            timeout_s: float,
        ) -> ModelTurn:
            raise ProviderError(
                "provider_rate_limited",
                "Provider rate limited the request.",
                retryable=True,
            )

    manifest = load_eval_manifest(MANIFEST_PATH)
    report = await run_live_suite(
        manifest,
        provider=RateLimitedProvider(),
        provider_name="anthropic",
        model="claude-sonnet-5",
        application_version="0.1.0",
        case_ids={"G-01"},
    )

    assert report.summary.total == 1
    assert report.summary.passed == 0
    assert report.summary.failed == 0
    assert report.summary.inconclusive_infra == 1
    assert report.results[0].outcome == "inconclusive_infra"
    assert report.results[0].observed.error_codes == ["provider_rate_limited"]


@pytest.mark.anyio
async def test_live_lane_can_pace_cases_without_retrying_a_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ask_seoul_agent import live_eval
    from ask_seoul_agent.evaluation import load_eval_manifest
    from ask_seoul_agent.providers.demo import DemoProvider

    observed_delays: list[float] = []

    async def record_delay(delay_s: float) -> None:
        observed_delays.append(delay_s)

    monkeypatch.setattr(live_eval.asyncio, "sleep", record_delay)
    report = await live_eval.run_live_suite(
        load_eval_manifest(MANIFEST_PATH),
        provider=DemoProvider(),
        provider_name="paced-provider",
        model="fixture-model",
        application_version="0.1.0",
        case_ids={"G-01", "G-02"},
        inter_case_delay_s=75.0,
    )

    assert report.summary.total == 2
    assert observed_delays == [75.0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case_ids", "expected_message"),
    [
        (set(), "at least one live case_id"),
        ({"NO-SUCH"}, "unknown live evaluation case_id"),
        ({"A-01"}, "not live-capable"),
    ],
)
async def test_live_lane_rejects_invalid_case_selection_before_provider_call(
    case_ids: set[str],
    expected_message: str,
) -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest
    from ask_seoul_agent.live_eval import run_live_suite

    manifest = load_eval_manifest(MANIFEST_PATH)

    with pytest.raises(ValueError, match=expected_message):
        await run_live_suite(
            manifest,
            provider=ProviderMustNotBeCalled(),
            provider_name="anthropic",
            model="claude-sonnet-5",
            application_version="0.1.0",
            case_ids=case_ids,
        )
