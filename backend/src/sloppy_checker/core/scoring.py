from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .methodology import content_allows, load_methodology
from .schemas import (
    ContentLevel,
    ContextAssessment,
    Coverage,
    DimensionScore,
    Finding,
    ModuleStatus,
    RubricGrade,
)


@dataclass(frozen=True)
class ScoreResult:
    composite: float
    uncapped: float
    dimensions: list[DimensionScore]
    coverage: Coverage
    banners: list[str]
    module_statuses: list[ModuleStatus]
    weighted_coverage: float


def score_findings(
    findings: list[Finding],
    context: ContextAssessment,
    content_level: ContentLevel = ContentLevel.FULL_TEXT,
    module_failures: dict[str, str] | None = None,
    reviewer_completed: bool = True,
) -> ScoreResult:
    methodology = load_methodology().definition
    grade_scores = {
        RubricGrade(key): float(value) for key, value in methodology.grade_scores.items()
    }
    failures = module_failures or {}
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if finding.critic_disposition != "discarded":
            grouped[finding.category].append(finding)

    dimensions: list[DimensionScore] = []
    statuses: list[ModuleStatus] = []
    weighted = 0.0
    assessed_weight = 0.0
    assessed_full_items = 0
    assessed_eligible_items = 0
    eligible_items = 0
    eligible_weight = 0.0
    total_items = sum(len(module.items) for module in methodology.modules)

    for module in methodology.modules:
        eligible = content_allows(content_level, module.minimum_content_level)
        expected = len(module.items)
        if eligible:
            eligible_items += expected
            eligible_weight += module.weight
        assessed_by_item = {
            finding.rubric_item: finding
            for finding in grouped[module.key]
            if finding.grade != RubricGrade.NOT_ASSESSED and finding.rubric_item in module.items
        }
        assessed = list(assessed_by_item.values())
        assessed_count = len(assessed)
        if eligible:
            assessed_eligible_items += assessed_count
        assessed_full_items += assessed_count
        value = (
            sum(grade_scores[finding.grade] for finding in assessed) / len(assessed)
            if assessed
            else 0.0
        )
        if assessed:
            item_weight = module.weight / expected
            weighted += sum(grade_scores[finding.grade] * item_weight for finding in assessed)
            assessed_weight += assessed_count * item_weight
        dimensions.append(
            DimensionScore(
                key=module.key,
                label=module.label,
                weight=module.weight,
                score=round(value, 1),
                assessed_items=assessed_count,
                total_items=expected,
            )
        )
        if not eligible:
            state = "ineligible_at_content_level"
            limitation = f"Requires {module.minimum_content_level.value}."
        elif module.key in failures:
            state = "module_failed"
            limitation = failures[module.key]
        elif not reviewer_completed:
            state = "unreviewed"
            limitation = "Independent reviewer did not complete."
        else:
            state = "completed"
            limitation = None
        statuses.append(
            ModuleStatus(
                key=module.key,
                label=module.label,
                state=state,
                assessed_items=assessed_count,
                expected_items=expected,
                limitation=limitation,
            )
        )

    score = weighted / assessed_weight if assessed_weight else 0.0
    available = assessed_eligible_items / eligible_items if eligible_items else 0.0
    weighted_coverage = assessed_weight / eligible_weight if eligible_weight else 0.0
    full_review = assessed_full_items / total_items if total_items else 0.0
    provisional = (
        full_review < float(methodology.score["provisional_full_review_coverage_below"])
        or bool(failures)
        or not reviewer_completed
    )
    limitations: list[str] = []
    if content_level != ContentLevel.FULL_TEXT:
        limitations.append(f"The analysis used {content_level.value.replace('_', ' ')} content, not the full paper.")
    if failures:
        limitations.append("One or more methodology modules failed and remain visibly incomplete.")
    if not reviewer_completed:
        limitations.append("Findings were not independently reviewed.")
    if provisional:
        limitations.append("Low full-review coverage makes the Review score provisional.")

    banners: list[str] = []
    if context.retracted:
        banners.append("Publication record reports a retraction; this status does not mathematically alter the Review score.")
    if context.expression_of_concern:
        banners.append("Publication record reports an expression of concern; inspect the sourced notice.")
    if context.corrections:
        banners.append("Corrections exist; conclusions should be checked against the latest version.")

    return ScoreResult(
        composite=round(score, 1),
        uncapped=round(score, 1),
        dimensions=dimensions,
        coverage=Coverage(
            paper=round(full_review, 3),
            context=round(
                sum(status.assessed_items for status in statuses if status.key in {"record", "disclosures"})
                / max(1, sum(status.expected_items for status in statuses if status.key in {"record", "disclosures"})),
                3,
            ),
            overall=round(full_review, 3),
            available=round(available, 3),
            full_review=round(full_review, 3),
            provisional=provisional,
            limitations=limitations,
        ),
        banners=banners,
        module_statuses=statuses,
        weighted_coverage=round(weighted_coverage, 3),
    )
