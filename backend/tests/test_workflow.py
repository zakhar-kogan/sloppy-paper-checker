from sloppy_checker.core.schemas import RubricProfile
from sloppy_checker.workflows.analysis import (
    baseline_findings,
    build_agno_workflow,
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
    assert len(findings) == 5
    assert all(f.severity.value == "info" for f in findings)
    assert all(f.paper_spans or f.grade.value == "not_assessed" for f in findings)


def test_agno_workflow_factory_has_parallel_specialists_and_critic():
    workflow = build_agno_workflow(
        {"base_url": "https://example.com/v1", "worker_model": "fast", "critic_model": "big"},
        "test-key-not-used",
    )
    assert workflow.name == "Sloppy Paper Checker v1"
    assert len(workflow.steps) == 2

