from __future__ import annotations

from dataclasses import dataclass

from .schemas import RubricProfile


@dataclass(frozen=True)
class RubricDefinition:
    profile: RubricProfile
    reporting_reference: str
    appraisal_reference: str
    design_items: tuple[str, ...]
    statistics_items: tuple[str, ...]


RUBRICS: dict[RubricProfile, RubricDefinition] = {
    RubricProfile.RANDOMIZED: RubricDefinition(
        RubricProfile.RANDOMIZED,
        "CONSORT / EQUATOR",
        "RoB 2 concepts (not a substituted RoB 2 assessment)",
        ("randomization process", "allocation concealment", "deviations", "outcome measurement", "attrition"),
        ("intention-to-treat", "power", "effect size and interval", "multiplicity", "missing data"),
    ),
    RubricProfile.OBSERVATIONAL: RubricDefinition(
        RubricProfile.OBSERVATIONAL,
        "STROBE / EQUATOR",
        "ROBINS-I concepts (not a substituted ROBINS-I assessment)",
        ("selection", "exposure measurement", "confounding", "comparators", "outcome measurement"),
        ("model specification", "overlap", "sensitivity analysis", "missing data", "uncertainty"),
    ),
    RubricProfile.QUALITATIVE: RubricDefinition(
        RubricProfile.QUALITATIVE,
        "Relevant EQUATOR qualitative guideline",
        "Method-specific qualitative appraisal",
        ("sampling rationale", "researcher reflexivity", "data collection", "saturation or information power", "analytic trace"),
        ("coding process", "triangulation", "negative cases", "participant validation", "uncertainty framing"),
    ),
    RubricProfile.SYSTEMATIC_REVIEW: RubricDefinition(
        RubricProfile.SYSTEMATIC_REVIEW,
        "PRISMA / EQUATOR",
        "AMSTAR 2 concepts (not a substituted AMSTAR 2 assessment)",
        ("protocol", "search coverage", "duplicate selection", "risk-of-bias process", "excluded studies"),
        ("effect model", "heterogeneity", "publication bias", "sensitivity analysis", "certainty interpretation"),
    ),
    RubricProfile.DIAGNOSTIC: RubricDefinition(
        RubricProfile.DIAGNOSTIC,
        "STARD/TRIPOD as applicable",
        "QUADAS-3 concepts for diagnostic accuracy",
        ("participant spectrum", "index test", "reference standard", "flow and timing", "validation split"),
        ("calibration", "discrimination", "threshold selection", "optimism correction", "external validation"),
    ),
    RubricProfile.COMPUTATIONAL: RubricDefinition(
        RubricProfile.COMPUTATIONAL,
        "Relevant EQUATOR/field reporting guideline",
        "Task- and domain-specific methodological appraisal",
        ("dataset provenance", "leakage", "baseline choice", "ablation", "external validity"),
        ("test-set isolation", "hyperparameter search", "uncertainty", "multiple benchmarks", "replication artifacts"),
    ),
    RubricProfile.COMMON_CORE: RubricDefinition(
        RubricProfile.COMMON_CORE,
        "Relevant EQUATOR guideline when identifiable",
        "No specialist appraisal profile selected",
        ("question clarity", "argument structure", "source relevance"),
        ("quantitative claims", "uncertainty", "internal consistency"),
    ),
}


def rubric_prompt(profile: RubricProfile) -> str:
    rubric = RUBRICS[profile]
    return (
        f"Reporting reference: {rubric.reporting_reference}. Appraisal reference: "
        f"{rubric.appraisal_reference}. Design items: {', '.join(rubric.design_items)}. "
        f"Analysis items: {', '.join(rubric.statistics_items)}."
    )

