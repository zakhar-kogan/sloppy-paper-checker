import json
from pathlib import Path

from sloppy_checker.core.evaluation import evaluate_report

CORPUS = Path(__file__).parents[2] / "evaluation" / "corpus"


def test_cross_domain_regression_fixtures_are_executable():
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    reports = json.loads((CORPUS / "regression_reports.json").read_text())
    cases = {case["id"]: case for case in manifest["cases"]}

    assert set(reports) == set(cases)
    for case_id, report in reports.items():
        metrics = evaluate_report(report, cases[case_id])
        assert metrics["profile_match"], case_id
        assert metrics["source_format_match"], case_id
        assert metrics["expected_evidence_recall"] == 1, case_id
        assert metrics["expected_finding_recall"] == 1, case_id
        assert metrics["false_absence_rate"] == 0, case_id
        assert metrics["unsupported_finding_rate"] == 0, case_id
        assert metrics["forbidden_assessed_items"] == [], case_id


def test_false_absence_and_cross_domain_item_leakage_are_measured():
    case = {
        "id": "failure",
        "profile": "computational_ml_modeling",
        "expected_evidence": [
            {"id": "dataset", "rubric_item": "dataset_provenance", "terms": ["dataset"]}
        ],
        "forbidden_assessed_items": ["missing_data"],
    }
    report = {
        "profile": "computational_ml_modeling",
        "evidence_notes": [
            {
                "rubric_item": "dataset_provenance",
                "observation": "Dataset provenance was not found.",
                "evidence_state": "not_found",
            }
        ],
        "findings": [
            {
                "rubric_item": "missing_data",
                "grade": "major_concern",
                "paper_spans": [{"quote": "Unrelated clinical text."}],
            }
        ],
    }
    metrics = evaluate_report(report, case)
    assert metrics["false_absence_rate"] == 1
    assert metrics["forbidden_assessed_items"] == ["missing_data"]
