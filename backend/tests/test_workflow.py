import json
from types import SimpleNamespace

import pytest

from sloppy_checker.core.schemas import ContentLevel, RubricGrade, RubricProfile
from sloppy_checker.workflows import analysis as analysis_module
from sloppy_checker.workflows.analysis import (
    _coerce_worker_evidence,
    _parse_final_assessment,
    adjudicate_assessment,
    baseline_findings,
    classify_profile,
)


def test_profile_routing():
    assert classify_profile("We conducted a randomized controlled trial") == RubricProfile.RANDOMIZED
    assert classify_profile("A systematic review and meta-analysis") == RubricProfile.SYSTEMATIC_REVIEW
    assert classify_profile("A neural network simulation study") == RubricProfile.COMPUTATIONAL
    assert classify_profile("A theoretical perspective") == RubricProfile.COMMON_CORE


def test_baseline_is_conservative_and_traceable():
    findings = baseline_findings(
        "Abstract. Methods. Participants. Statistical analysis used confidence intervals. Results. Discussion. Data availability: repository.",
        RubricProfile.OBSERVATIONAL,
    )
    assert len(findings) == 28
    assert all(f.severity.value == "info" for f in findings)
    assert all(not f.paper_spans and f.grade.value == "not_assessed" for f in findings)


def test_worker_output_accepts_common_openai_compatible_array_shape_without_grading():
    output = _coerce_worker_evidence(
        [
            {
                "rubric_item": "design_identification",
                "judgment": "critical_concern",
                "evidence": "We conducted a randomized controlled trial.",
                "reasoning": "The study design is named explicitly.",
            }
        ],
        "design",
        ["design_identification"],
        "Methods. We conducted a randomized controlled trial. Results.",
    )
    assert output.items[0].quotes == ["We conducted a randomized controlled trial."]
    assert "grade" not in output.items[0].model_dump()


def test_malformed_worker_output_is_preserved_as_text():
    output = _coerce_worker_evidence(
        "Useful notes, but definitely not JSON: funding is described in acknowledgments.",
        "record",
        ["identity_and_version"],
        "Title and abstract only.",
    )
    assert not output.items
    assert output.raw_text.startswith("Useful notes")


def test_empty_worker_output_is_not_mistaken_for_evidence():
    output = _coerce_worker_evidence(None, "record", ["identity_consistency"], "Paper")
    assert output.raw_text == ""
    assert not output.items


def test_final_assessment_is_the_only_source_of_grades_and_checks_quotes():
    output = _parse_final_assessment(
        {
            "assessments": [
                {
                    "rubric_item": "study_design",
                    "grade": "no_concern",
                    "title": "Study design is stated",
                    "explanation": "The paper identifies its design.",
                    "confidence": 0.9,
                    "evidence_quotes": ["The study used a randomized design."],
                    "affected_conclusions": [],
                    "counterevidence": [],
                    "limitations": [],
                },
                {
                    "rubric_item": "sampling",
                    "grade": "major_concern",
                    "title": "Unsupported sampling judgment",
                    "explanation": "This quote is absent.",
                    "confidence": 0.8,
                    "evidence_quotes": ["Not in the normalized paper."],
                    "affected_conclusions": [],
                    "counterevidence": [],
                    "limitations": [],
                },
            ],
            "summary": ["Two items were returned."],
        },
        "The study used a randomized design.",
    )
    by_item = {finding.rubric_item: finding for finding in output.findings}
    assert by_item["study_design"].grade == RubricGrade.NO_CONCERN
    assert by_item["sampling"].grade == RubricGrade.NOT_ASSESSED
    assert output.assessed_attempts == 2
    assert output.grounded_assessed == 1
    assert "comparators" in output.missing_item_ids


def test_final_assessment_reports_duplicate_and_unknown_item_ids():
    decision = {
        "rubric_item": "study_design",
        "grade": "no_concern",
        "title": "Study design",
        "explanation": "The design is stated.",
        "confidence": 0.8,
        "evidence_quotes": ["Randomized study."],
        "affected_conclusions": [],
        "counterevidence": [],
        "limitations": [],
    }
    unknown = {**decision, "rubric_item": "invented_item"}
    output = _parse_final_assessment(
        {"assessments": [decision, decision, unknown], "summary": []},
        "Randomized study.",
    )
    assert len(output.findings) == 1
    assert any("Duplicate" in warning for warning in output.validation_warnings)
    assert any("Unknown" in warning for warning in output.validation_warnings)


@pytest.mark.asyncio
async def test_final_assessment_gets_one_format_repair_and_preserves_partial_result(monkeypatch):
    valid_partial = json.dumps(
        {
            "assessments": [
            {
                    "rubric_item": "study_design",
                    "grade": "no_concern",
                    "title": "Study design is stated",
                    "explanation": "The paper identifies its design.",
                    "confidence": 0.9,
                    "evidence_quotes": ["The study used a randomized design."],
                    "affected_conclusions": [],
                    "counterevidence": [],
                    "limitations": [],
                }
            ],
            "summary": ["Partial but usable assessment."],
        }
    )
    responses = iter(
        [
            SimpleNamespace(content="{malformed", metrics=None),
            SimpleNamespace(content=valid_partial, metrics=None),
        ]
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, prompt):
            return next(responses)

    monkeypatch.setattr(analysis_module, "Agent", FakeAgent)
    result = await adjudicate_assessment(
        "The study used a randomized design.",
        RubricProfile.RANDOMIZED,
        ContentLevel.FULL_TEXT,
        [],
        {"reviewer_model": "reviewer", "worker_model": "worker"},
        "unused-key",
    )
    assert result.repaired_output
    assert result.findings[0].grade == RubricGrade.NO_CONCERN
    assert len(result.missing_item_ids) == 27
