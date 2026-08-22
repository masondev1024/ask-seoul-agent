"""Opt-in live-provider evaluation over deterministic recorded tool fixtures."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Collection, Mapping
from typing import Any
from uuid import uuid4

from .agent import AgentRunner
from .eval_runtime import build_recorded_fixture, derive_event_checks
from .evaluation import (
    EvalCase,
    EvalCaseResult,
    EvalManifest,
    EvalReport,
    build_eval_report,
    evaluate_events,
)
from .providers.base import LLMProvider
from .tools import ToolRegistry

# These failures prevent the harness from judging model behaviour. Model
# refusals, invalid tool choices, protocol errors, and budget failures are
# deliberately absent: those are behavioural outcomes and must remain fails.
_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {
        "agent_timeout",
        "provider_auth",
        "provider_connection",
        "provider_error",
        "provider_rate_limited",
        "provider_timeout",
        "provider_unavailable",
        "upstream_retry_exhausted",
        "upstream_retryable_status",
        "upstream_timeout",
        "upstream_transport_error",
    }
)


def select_live_cases(
    manifest: EvalManifest,
    case_ids: Collection[str] | None = None,
) -> list[EvalCase]:
    """Validate and return live-capable cases in manifest order."""

    live_case_ids = {case.case_id for case in manifest.cases if "live" in case.run_modes}
    if case_ids is not None:
        requested = set(case_ids)
        if not requested:
            raise ValueError("at least one live case_id must be provided")

        known_case_ids = {case.case_id for case in manifest.cases}
        unknown = sorted(requested - known_case_ids)
        if unknown:
            raise ValueError(f"unknown live evaluation case_id: {', '.join(unknown)}")

        non_live = sorted(requested - live_case_ids)
        if non_live:
            raise ValueError(f"case_id not live-capable: {', '.join(non_live)}")

        return [
            case
            for case in manifest.cases
            if "live" in case.run_modes and case.case_id in requested
        ]

    selected = [case for case in manifest.cases if "live" in case.run_modes]
    if not selected:
        raise ValueError("manifest contains no live-capable cases")
    return selected


async def run_live_suite(
    manifest: EvalManifest,
    *,
    provider: LLMProvider,
    provider_name: str,
    model: str | None,
    application_version: str,
    case_ids: Collection[str] | None = None,
    inter_case_delay_s: float = 0.0,
) -> EvalReport:
    """Run selected live cases once, serially, against recorded tool data.

    The caller-owned provider instance is shared across cases so its connection
    pool and rate-limit state are reused. Deliberate serial execution keeps the
    effective provider concurrency at one. Neither questions, answers, nor
    evidence rows enter the returned report; ``evaluate_events`` retains only a
    bounded observation summary.
    """

    selected = select_live_cases(manifest, case_ids)
    if (
        not math.isfinite(inter_case_delay_s)
        or inter_case_delay_s < 0
        or inter_case_delay_s > 300
    ):
        raise ValueError("inter_case_delay_s must be between 0 and 300 seconds")
    results: list[EvalCaseResult] = []

    for case_index, case in enumerate(selected):
        if case_index > 0 and inter_case_delay_s > 0:
            await asyncio.sleep(inter_case_delay_s)
        fixture_id = case.fixture_id
        if fixture_id is None:  # Defensive backstop for programmatically built manifests.
            raise ValueError(f"live evaluation case has no fixture: {case.case_id}")

        fixture = build_recorded_fixture(fixture_id)
        runner = AgentRunner(
            provider=provider,
            tools=ToolRegistry(ask_seoul=fixture),
            provider_name=provider_name,
            max_rounds=case.budgets.max_rounds,
            max_tool_calls=case.budgets.max_tool_calls,
            total_timeout_s=case.budgets.timeout_s,
            provider_timeout_s=min(30.0, case.budgets.timeout_s),
        )
        trace_id = f"live-{case.case_id.lower()}-{uuid4().hex[:16]}"
        events: list[Mapping[str, Any]] = [
            event
            async for event in runner.stream(
                case.question.strip(),
                trace_id=trace_id,
            )
        ]
        checks = derive_event_checks(case, events, fixture=fixture)
        result = evaluate_events(case, events, mode="live", checks=checks)
        observed = result.observed.model_copy(
            update={
                "provider_call_count": sum(
                    event.get("event") == "assistant.status"
                    and event.get("status") == "requesting_model_turn"
                    for event in events
                ),
                "upstream_call_count": len(fixture.calls),
            }
        )
        result = result.model_copy(update={"observed": observed})
        if _has_infrastructure_failure(result):
            result = result.model_copy(update={"outcome": "inconclusive_infra"})
        results.append(result)

    return build_eval_report(
        manifest,
        mode="live",
        results=results,
        provider_name=provider_name,
        model=model,
        application_version=application_version,
    )


def _has_infrastructure_failure(result: EvalCaseResult) -> bool:
    return any(code in _INFRASTRUCTURE_ERROR_CODES for code in result.observed.error_codes)
