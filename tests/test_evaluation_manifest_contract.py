import json
from pathlib import Path

import pytest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.v1.json"


def test_eval_manifest_is_versioned_strict_and_contains_30_unique_cases() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)

    assert manifest.schema_version == 1
    assert manifest.dataset_version == "2026-08-23.1"
    assert len(manifest.cases) == 30
    assert len({case.case_id for case in manifest.cases}) == 30
    assert {case.case_type for case in manifest.cases} == {"golden", "red_team"}
    assert sum(case.case_type == "golden" for case in manifest.cases) >= 10
    assert sum(case.case_type == "red_team" for case in manifest.cases) >= 10
    assert all(case.expected.allowed_tool_paths for case in manifest.cases)
    assert all(case.expected.invariants for case in manifest.cases)


def test_every_live_case_has_a_recorded_fixture_and_bounded_contract() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    live_cases = [case for case in manifest.cases if "live" in case.run_modes]

    assert len(live_cases) >= 10
    assert all(case.fixture_id is not None for case in live_cases)
    assert all(case.budgets.max_rounds <= 4 for case in live_cases)
    assert all(case.budgets.max_tool_calls <= 4 for case in live_cases)
    assert all(case.budgets.max_output_tokens <= 1024 for case in live_cases)
    assert all(case.budgets.timeout_s <= 45 for case in live_cases)


def test_tool_path_invariants_cannot_contradict_allowed_paths() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    for case in manifest.cases:
        expectation = case.expected
        if "search_is_not_evidence" in expectation.invariants:
            assert all("search_products" in path for path in expectation.allowed_tool_paths)
        if "empty_rows_not_evidence" in expectation.invariants:
            assert all("preview_product" in path for path in expectation.allowed_tool_paths)


def test_empty_search_questions_do_not_leak_the_expected_outcome() -> None:
    from ask_seoul_agent.evaluation import load_eval_manifest

    manifest = load_eval_manifest(MANIFEST_PATH)
    empty_search_cases = [case for case in manifest.cases if case.category == "empty_search"]

    assert empty_search_cases
    assert all("존재하지 않는" not in case.question for case in empty_search_cases)


def test_manifest_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    from ask_seoul_agent.evaluation import EvalManifestError, load_eval_manifest

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"].append(payload["cases"][0])
    invalid = tmp_path / "duplicate.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalManifestError, match="duplicate case_id"):
        load_eval_manifest(invalid)


def test_manifest_rejects_unknown_fields_instead_of_ignoring_contract_drift(
    tmp_path: Path,
) -> None:
    from ask_seoul_agent.evaluation import EvalManifestError, load_eval_manifest

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["unexpected_contract_field"] = True
    invalid = tmp_path / "drift.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalManifestError, match="invalid evaluation manifest"):
        load_eval_manifest(invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixture_id", "unregistered_fixture"),
        ("scenario_id", "unregistered_scenario"),
    ],
)
def test_manifest_rejects_unregistered_execution_contracts(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    from ask_seoul_agent.evaluation import EvalManifestError, load_eval_manifest

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"][0][field] = value
    invalid = tmp_path / f"unknown-{field}.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalManifestError, match="invalid evaluation manifest"):
        load_eval_manifest(invalid)
