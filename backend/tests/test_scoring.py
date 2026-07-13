
import pytest

from sloppy_checker.core.schemas import (
    ContextAssessment,
    Finding,
    FindingSeverity,
    PaperSpan,
    RubricGrade,
)
from sloppy_checker.core.scoring import score_findings


def finding(category: str, grade: RubricGrade) -> Finding:
    return Finding(
        id=f"{category}-{grade}",
        category=category,
        rubric_item="test",
        title="Test finding",
        explanation="Traceable test evidence.",
        severity=FindingSeverity.INFO,
        grade=grade,
        confidence=0.9,
        paper_spans=[PaperSpan(page=1, quote="Methods were prespecified.")],
    )


def complete_paper(grade: RubricGrade = RubricGrade.NO_CONCERN) -> list[Finding]:
    return [finding(key, grade) for key in ("design", "statistics", "claims", "transparency", "reporting")]


def test_missing_context_is_score_neutral_but_reduces_coverage():
    result = score_findings(complete_paper(), ContextAssessment())
    assert result.composite == 100
    assert result.coverage.paper == 1
    assert result.coverage.context == 0
    assert result.coverage.overall == pytest.approx(0.85)
    assert not result.coverage.provisional


def test_weighted_grade_conversion():
    findings = complete_paper()
    findings[0] = finding("design", RubricGrade.CRITICAL_CONCERN)
    result = score_findings(findings, ContextAssessment())
    assert result.composite == pytest.approx(70.6, abs=0.1)


@pytest.mark.parametrize(
    ("context", "cap"),
    [(ContextAssessment(retracted=True), 10), (ContextAssessment(expression_of_concern=True), 40)],
)
def test_serious_record_status_caps_score(context, cap):
    result = score_findings(complete_paper(), context)
    assert result.composite == cap
    assert result.uncapped == 100


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

