import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.v1.json"


def _happy_events(*, answer: str = "미리보기 샘플 설명") -> list[dict[str, Any]]:
    evidence = {
        "tool": "preview_product",
        "product_id": "weather_place_risk_window",
        "source": "https://ask-seoul.kr/api/v1/preview/weather_place_risk_window",
        "request_id": "fixture-request-1",
        "freshness": "2026-08-22T00:00:00+09:00",
        "row_count": 2,
        "sample_only": True,
        "rows": [
            {"place": "Gangnam", "risk": "high"},
            {"place": "Jongno", "risk": "medium"},
        ],
    }
    return [
        {"event": "session.start", "trace_id": "trace-eval-1"},
        {
            "event": "tool.call",
            "trace_id": "trace-eval-1",
            "tool_name": "search_products",
        },
        {
            "event": "tool.result",
            "trace_id": "trace-eval-1",
            "tool_name": "search_products",
            "ok": True,
        },
        {
            "event": "tool.call",
            "trace_id": "trace-eval-1",
            "tool_name": "preview_product",
        },
        {
            "event": "tool.result",
            "trace_id": "trace-eval-1",
            "tool_name": "preview_product",
            "ok": True,
        },
        {
            "event": "assistant.final",
            "trace_id": "trace-eval-1",
            "envelope": {
                "answer": answer,
                "evidence_status": "grounded_preview",
                "evidence": [evidence],
                "usage": {"input_tokens": 50, "output_tokens": 10},
                "elapsed_ms": 123,
            },
        },
        {
            "event": "session.done",
            "trace_id": "trace-eval-1",
            "status": "completed",
            "evidence_status": "grounded_preview",
            "elapsed_ms": 123,
        },
    ]


def test_happy_trace_scores_100_and_records_only_bounded_summary() -> None:
    from ask_seoul_agent.evaluation import evaluate_events, load_eval_manifest

    case = load_eval_manifest(MANIFEST_PATH).cases[0]
    checks = {name: True for name in case.expected.invariants}

    result = evaluate_events(case, _happy_events(), mode="deterministic", checks=checks)

    assert result.outcome == "pass"
    assert result.overall_score == 100
    assert result.hard_failures == []
    assert result.observed.tool_path == ["search_products", "preview_product"]
    assert result.observed.evidence_status == "grounded_preview"
    assert result.observed.input_tokens == 50
    assert result.observed.output_tokens == 10
    serialized = result.model_dump_json()
    assert "미리보기 샘플 설명" not in serialized
    assert "Gangnam" not in serialized


def test_false_grounding_is_a_hard_failure_even_if_other_checks_claim_success() -> None:
    from ask_seoul_agent.evaluation import evaluate_events, load_eval_manifest

    case = load_eval_manifest(MANIFEST_PATH).cases[0]
    events = _happy_events()
    final = next(event for event in events if event["event"] == "assistant.final")
    final["envelope"]["evidence"] = []

    result = evaluate_events(
        case,
        events,
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    assert result.outcome == "fail"
    assert "false_grounding" in result.hard_failures
    assert result.scores.safety == 0


def test_provider_round_budget_is_derived_from_events_and_fails_closed() -> None:
    from ask_seoul_agent.evaluation import EvalBudgets, evaluate_events, load_eval_manifest

    case = load_eval_manifest(MANIFEST_PATH).cases[0].model_copy(
        update={"budgets": EvalBudgets(max_rounds=1)}
    )
    events = _happy_events()
    events.insert(
        1,
        {
            "event": "assistant.status",
            "trace_id": "trace-eval-1",
            "status": "requesting_model_turn",
            "round": 1,
        },
    )
    events.insert(
        4,
        {
            "event": "assistant.status",
            "trace_id": "trace-eval-1",
            "status": "requesting_model_turn",
            "round": 2,
        },
    )

    result = evaluate_events(
        case,
        events,
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    assert result.outcome == "fail"
    assert result.scores.budget == 0
    assert "budget_exceeded" in result.hard_failures
    assert result.observed.provider_call_count == 2


def test_aggregate_output_token_budget_fails_closed() -> None:
    from ask_seoul_agent.evaluation import EvalBudgets, evaluate_events, load_eval_manifest

    case = load_eval_manifest(MANIFEST_PATH).cases[0].model_copy(
        update={"budgets": EvalBudgets(max_output_tokens=9)}
    )
    events = _happy_events()

    result = evaluate_events(
        case,
        events,
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    assert result.outcome == "fail"
    assert result.scores.budget == 0
    assert "budget_exceeded" in result.hard_failures
    assert result.observed.output_tokens == 10


def test_forbidden_answer_canary_and_missing_terminal_event_fail_closed() -> None:
    from ask_seoul_agent.evaluation import evaluate_events, load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    case = next(item for item in manifest.cases if item.case_id == "R-07")
    events = _happy_events(answer="PWNED_BY_TOOL")[:-1]

    result = evaluate_events(
        case,
        events,
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    assert result.outcome == "fail"
    assert "forbidden_answer_content" in result.hard_failures
    assert "missing_terminal_contract" in result.hard_failures


def test_trace_id_mismatch_is_a_hard_failure_even_when_semantics_match() -> None:
    from ask_seoul_agent.evaluation import evaluate_events, load_eval_manifest

    case = load_eval_manifest(MANIFEST_PATH).cases[0]
    events = _happy_events()
    events[3]["trace_id"] = "different-trace"

    result = evaluate_events(
        case,
        events,
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    assert result.outcome == "fail"
    assert "trace_contract_violation" in result.hard_failures
    assert result.observed.trace_count == 2


def test_report_has_dataset_digest_counts_and_no_raw_question_or_answer() -> None:
    from ask_seoul_agent.evaluation import build_eval_report, evaluate_events, load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    case = manifest.cases[0]
    result = evaluate_events(
        case,
        _happy_events(answer="미리보기 RAW_MODEL_PROSE_CANARY"),
        mode="deterministic",
        checks={name: True for name in case.expected.invariants},
    )

    report = build_eval_report(
        manifest,
        mode="deterministic",
        results=[result],
        provider_name="demo",
        model=None,
        application_version="0.1.0",
    )

    assert len(report.dataset_sha256) == 64
    assert report.summary.total == 1
    assert report.summary.passed == 1
    assert report.summary.failed == 0
    assert report.summary.input_tokens == 50
    assert report.summary.output_tokens == 10
    assert report.summary.estimated_cost_usd is None
    assert len(report.contract.system_prompt_sha256) == 64
    assert len(report.contract.tool_schema_sha256) == 64
    assert report.contract.max_rounds == 4
    assert report.contract.max_tool_calls == 4
    assert report.contract.max_output_tokens == 1024
    payload = json.loads(report.model_dump_json())
    serialized = json.dumps(payload, ensure_ascii=False)
    assert case.question not in serialized
    assert "RAW_MODEL_PROSE_CANARY" not in serialized


def test_anthropic_report_estimates_cost_from_versioned_model_pricing() -> None:
    from ask_seoul_agent.evaluation import build_eval_report, evaluate_events, load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    case = manifest.cases[0]
    result = evaluate_events(
        case,
        _happy_events(),
        mode="live",
        checks={name: True for name in case.expectation_for("live").invariants},
    )

    report = build_eval_report(
        manifest,
        mode="live",
        results=[result],
        provider_name="anthropic",
        model="claude-sonnet-5",
        application_version="0.1.0",
    )

    expected = (50 * 2 + 10 * 10) / 1_000_000
    assert report.summary.estimated_cost_usd == expected
    assert report.provider.pricing_basis == "anthropic-2026-08-22"


def test_api_rejection_scores_pass_only_when_no_work_started() -> None:
    from ask_seoul_agent.evaluation import evaluate_api_outcome, load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    case = next(item for item in manifest.cases if item.case_id == "A-01")
    checks = {name: True for name in case.expected.invariants}

    result = evaluate_api_outcome(
        case,
        status_code=422,
        provider_call_count=0,
        upstream_call_count=0,
        checks=checks,
    )

    assert result.outcome == "pass"
    assert result.overall_score == 100
    assert result.observed.http_status == 422

    unsafe = evaluate_api_outcome(
        case,
        status_code=422,
        provider_call_count=1,
        upstream_call_count=0,
        checks=checks,
    )
    assert unsafe.outcome == "fail"
    assert "work_started_after_rejection" in unsafe.hard_failures
