from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .schemas import ContextAssessment, Coverage, DimensionScore, Finding, RubricGrade

WEIGHTS = {
    "design": ("Design & reasoning", 25.0, "paper"),
    "statistics": ("Analysis & statistics", 20.0, "paper"),
    "claims": ("Claim–evidence alignment", 20.0, "paper"),
    "transparency": ("Transparency & reproducibility", 12.0, "paper"),
    "reporting": ("Reporting & internal consistency", 8.0, "paper"),
    "venue": ("Venue & record integrity", 6.0, "context"),
    "authors": ("Relevant author & conflict evidence", 5.0, "context"),
    "standing": ("Field-normalized journal standing", 4.0, "context"),
}

GRADE_SCORES = {
    RubricGrade.NO_CONCERN: 100.0,
    RubricGrade.MINOR_CONCERN: 75.0,
    RubricGrade.MAJOR_CONCERN: 35.0,
    RubricGrade.CRITICAL_CONCERN: 0.0,
}


@dataclass(frozen=True)
class ScoreResult:
    composite: float
    uncapped: float
    dimensions: list[DimensionScore]
    coverage: Coverage
    banners: list[str]


def score_findings(findings: list[Finding], context: ContextAssessment) -> ScoreResult:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if finding.critic_disposition != "discarded" and finding.category in WEIGHTS:
            grouped[finding.category].append(finding)

    paper_values: list[tuple[float, float]] = []
    dimensions: list[DimensionScore] = []
    for key, (label, weight, group) in WEIGHTS.items():
        assessed = [f for f in grouped[key] if f.grade != RubricGrade.NOT_ASSESSED]
        if assessed:
            value = sum(GRADE_SCORES[f.grade] for f in assessed) / len(assessed)
        else:
            value = 0.0
        if group == "paper" and assessed:
            paper_values.append((value, weight))
        dimensions.append(
            DimensionScore(
                key=key,
                label=label,
                weight=weight,
                score=round(value, 1),
                assessed_items=len(assessed),
                total_items=max(len(grouped[key]), 1),
            )
        )

    paper_score = (
        sum(value * weight for value, weight in paper_values)
        / sum(weight for _, weight in paper_values)
        if paper_values
        else 0.0
    )

    weighted = 0.0
    assessed_weight = 0.0
    paper_assessed = 0
    paper_total = 0
    context_assessed = 0
    context_total = 0
    neutralized: list[str] = []
    for dimension in dimensions:
        _, weight, group = WEIGHTS[dimension.key]
        if group == "paper":
            paper_total += dimension.total_items
            paper_assessed += dimension.assessed_items
            if dimension.assessed_items:
                weighted += dimension.score * weight
                assessed_weight += weight
        else:
            context_total += dimension.total_items
            context_assessed += dimension.assessed_items
            # Unknown context inherits paper score: neutral, but visibly uncovered.
            value = dimension.score if dimension.assessed_items else paper_score
            if not dimension.assessed_items:
                neutralized.append(dimension.label)
            weighted += value * weight
            assessed_weight += weight

    uncapped = weighted / assessed_weight if assessed_weight else 0.0
    composite = uncapped
    banners: list[str] = []
    if context.retracted:
        composite = min(composite, 10.0)
        banners.append("Retracted record: composite score capped at 10.")
    elif context.expression_of_concern:
        composite = min(composite, 40.0)
        banners.append("Expression of concern: composite score capped at 40.")
    if context.corrections:
        banners.append("Corrections exist; conclusions should be checked against the latest version.")

    paper_coverage = paper_assessed / paper_total if paper_total else 0.0
    context_coverage = context_assessed / context_total if context_total else 0.0
    overall_coverage = paper_coverage * 0.85 + context_coverage * 0.15
    provisional = overall_coverage < 0.7
    limitations = []
    if neutralized:
        limitations.append("Missing context was score-neutral: " + ", ".join(neutralized) + ".")
    if provisional:
        limitations.append("Low evidence coverage makes this result provisional.")

    return ScoreResult(
        composite=round(composite, 1),
        uncapped=round(uncapped, 1),
        dimensions=dimensions,
        coverage=Coverage(
            paper=round(paper_coverage, 3),
            context=round(context_coverage, 3),
            overall=round(overall_coverage, 3),
            provisional=provisional,
            limitations=limitations,
        ),
        banners=banners,
    )
