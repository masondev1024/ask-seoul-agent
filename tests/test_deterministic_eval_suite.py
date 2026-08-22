from pathlib import Path

import pytest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.v1.json"


@pytest.mark.anyio
async def test_deterministic_eval_executes_all_30_cases_as_a_release_gate() -> None:
    from ask_seoul_agent.eval_runtime import run_deterministic_suite
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    report = await run_deterministic_suite(manifest, application_version="0.1.0")

    assert report.mode == "deterministic"
    assert report.provider.name == "scripted"
    assert report.summary.total == 30
    assert report.summary.passed == 30
    assert report.summary.failed == 0
    assert report.summary.inconclusive_infra == 0
    assert {result.case_id for result in report.results} == {
        case.case_id for case in manifest.cases
    }
    assert all(result.overall_score == 100 for result in report.results)
    assert all(result.hard_failures == [] for result in report.results)


@pytest.mark.anyio
async def test_deterministic_report_is_reproducible_except_run_identity_and_timing() -> None:
    from ask_seoul_agent.eval_runtime import run_deterministic_suite
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    first = await run_deterministic_suite(manifest, application_version="0.1.0")
    second = await run_deterministic_suite(manifest, application_version="0.1.0")

    assert first.dataset_sha256 == second.dataset_sha256
    first_results = [
        result.model_dump(exclude={"observed": {"elapsed_ms"}}) for result in first.results
    ]
    assert first_results == [
        result.model_dump(exclude={"observed": {"elapsed_ms"}}) for result in second.results
    ]
