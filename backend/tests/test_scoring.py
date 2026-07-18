import pytest

from sloppy_checker.core.methodology import load_methodology
from sloppy_checker.core.rubrics import rubric_items
from sloppy_checker.core.schemas import (
    ContentLevel,
    ContextAssessment,
    Finding,
    FindingSeverity,
    PaperSpan,
    RubricGrade,
    RubricProfile,
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


def complete_paper(
    grade: RubricGrade = RubricGrade.NO_CONCERN,
    profile: RubricProfile = RubricProfile.GENERAL_EMPIRICAL,
) -> list[Finding]:
    return [
        finding(module.key, item, grade)
        for module in load_methodology().definition.modules
        for item in rubric_items(profile, module.key, module.items)
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
    findings[0] = finding("design", "study_question_design", RubricGrade.CRITICAL_CONCERN)
    result = score_findings(findings, ContextAssessment())
    assert result.composite == pytest.approx(94, abs=0.1)


def test_partial_score_is_renormalized_and_weighted_coverage_is_separate():
    result = score_findings(
        [finding("design", "study_question_design", RubricGrade.NO_CONCERN)],
        ContextAssessment(),
    )
    assert result.composite == 100
    assert result.weighted_coverage == 0.06
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
        if item.category in {"claims", "disclosures"}
    ]
    result = score_findings(eligible, ContextAssessment(), ContentLevel.ABSTRACT)
    assert result.coverage.available == 1
    assert result.coverage.full_review < 0.7
    assert result.coverage.provisional
    assert {status.state for status in result.module_statuses if status.key in {"design", "statistics", "transparency"}} == {
        "ineligible_at_content_level"
    }


@pytest.mark.parametrize("profile", list(RubricProfile))
def test_profile_specific_design_and_statistics_items_are_scored(profile):
    result = score_findings(
        complete_paper(profile=profile),
        ContextAssessment(),
        profile=profile,
    )
    assert result.coverage.full_review == 1
    expected = {
        module.key: set(rubric_items(profile, module.key, module.items))
        for module in load_methodology().definition.modules
    }
    assert all(status.expected_items == len(expected[status.key]) for status in result.module_statuses)


def test_systematic_review_statistics_include_imputation_and_heterogeneity():
    methodology = load_methodology().definition
    statistics = next(module for module in methodology.modules if module.key == "statistics")
    items = rubric_items(RubricProfile.SYSTEMATIC_REVIEW, statistics.key, statistics.items)
    assert "heterogeneity" in items
    assert "response_imputation" in items


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
