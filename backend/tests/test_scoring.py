import pytest

from sloppy_checker.core.methodology import load_methodology
from sloppy_checker.core.schemas import (
    ContentLevel,
    ContextAssessment,
    Finding,
    FindingSeverity,
    PaperSpan,
    RubricGrade,
)
from sloppy_checker.core.scoring import score_findings


def finding(category: str, rubric_item: str, grade: RubricGrade) -> Finding:
    return Finding(
        id=f"{category}-{rubric_item}-{grade}",
        category=category,
        rubric_item=rubric_item,
        title="Test finding",
        explanation="Traceable test evidence.",
        severity=FindingSeverity.INFO,
        grade=grade,
        confidence=0.9,
        paper_spans=[PaperSpan(page=1, quote="Methods were prespecified.")],
    )


def complete_paper(grade: RubricGrade = RubricGrade.NO_CONCERN) -> list[Finding]:
    return [
        finding(module.key, item, grade)
        for module in load_methodology().definition.modules
        for item in module.items
    ]


def test_full_review_uses_fixed_expected_item_denominator():
    result = score_findings(complete_paper(), ContextAssessment())
    assert result.composite == 100
    assert result.coverage.paper == 1
    assert result.coverage.available == 1
    assert result.coverage.full_review == 1
    assert not result.coverage.provisional


def test_weighted_item_grade_conversion():
    findings = complete_paper()
    findings[0] = finding("design", "study_design", RubricGrade.CRITICAL_CONCERN)
    result = score_findings(findings, ContextAssessment())
    assert result.composite == pytest.approx(95, abs=0.1)


def test_partial_score_is_renormalized_and_weighted_coverage_is_separate():
    result = score_findings(
        [finding("design", "study_design", RubricGrade.NO_CONCERN)],
        ContextAssessment(),
    )
    assert result.composite == 100
    assert result.weighted_coverage == 0.05
    assert result.coverage.provisional


@pytest.mark.parametrize(
    "context",
    [ContextAssessment(retracted=True), ContextAssessment(expression_of_concern=True)],
)
def test_record_status_is_a_banner_and_never_caps_score(context):
    result = score_findings(complete_paper(), context)
    assert result.composite == 100
    assert result.uncapped == 100
    assert result.banners


def test_abstract_content_gates_full_text_modules_and_marks_score_provisional():
    eligible = [
        item
        for item in complete_paper()
        if item.category in {"claims", "record", "disclosures"}
    ]
    result = score_findings(eligible, ContextAssessment(), ContentLevel.ABSTRACT)
    assert result.coverage.available == 1
    assert result.coverage.full_review < 0.7
    assert result.coverage.provisional
    assert {status.state for status in result.module_statuses if status.key in {"design", "statistics", "transparency"}} == {
        "ineligible_at_content_level"
    }


def test_substantive_finding_requires_evidence():
    with pytest.raises(ValueError, match="require a paper span"):
        Finding(
            id="unsupported",
            category="claims",
            rubric_item="causal_claim",
            title="Unsupported allegation",
            explanation="No source.",
            severity=FindingSeverity.MAJOR,
            grade=RubricGrade.MAJOR_CONCERN,
            confidence=0.9,
        )
