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
    RubricProfile.GENERAL_EMPIRICAL: RubricDefinition(
        RubricProfile.GENERAL_EMPIRICAL,
        "Relevant EQUATOR guideline when identifiable",
        "General empirical-design and risk-of-bias concepts",
        (
            "study_question_design",
            "sampling_eligibility",
            "measurement_quality",
            "comparators_controls",
            "threats_to_validity",
        ),
        (
            "analysis_specification",
            "effect_size_uncertainty",
            "missing_data",
            "robustness_checks",
            "claim_calibration",
        ),
    ),
    RubricProfile.RANDOMIZED: RubricDefinition(
        RubricProfile.RANDOMIZED,
        "CONSORT / EQUATOR",
        "RoB 2 concepts (not a substituted RoB 2 assessment)",
        ("randomization_process", "allocation_concealment", "deviations", "outcome_measurement", "attrition"),
        ("intention_to_treat", "power", "effect_size_interval", "multiplicity", "missing_data"),
    ),
    RubricProfile.OBSERVATIONAL: RubricDefinition(
        RubricProfile.OBSERVATIONAL,
        "STROBE / EQUATOR",
        "ROBINS-I concepts (not a substituted ROBINS-I assessment)",
        ("selection", "exposure_measurement", "confounding", "comparators", "outcome_measurement"),
        ("model_specification", "overlap", "sensitivity_analysis", "missing_data", "uncertainty"),
    ),
    RubricProfile.QUALITATIVE: RubricDefinition(
        RubricProfile.QUALITATIVE,
        "Relevant EQUATOR qualitative guideline",
        "Method-specific qualitative appraisal",
        ("sampling_rationale", "researcher_reflexivity", "data_collection", "information_power", "analytic_trace"),
        ("coding_process", "triangulation", "negative_cases", "participant_validation", "uncertainty_framing"),
    ),
    RubricProfile.SYSTEMATIC_REVIEW: RubricDefinition(
        RubricProfile.SYSTEMATIC_REVIEW,
        "PRISMA / EQUATOR",
        "AMSTAR 2 concepts (not a substituted AMSTAR 2 assessment)",
        ("protocol", "search_coverage", "duplicate_selection", "risk_of_bias_process", "excluded_studies"),
        ("effect_model", "heterogeneity", "response_imputation", "publication_bias", "sensitivity_analysis"),
    ),
    RubricProfile.DIAGNOSTIC: RubricDefinition(
        RubricProfile.DIAGNOSTIC,
        "STARD/TRIPOD as applicable",
        "QUADAS-3 concepts for diagnostic accuracy",
        ("participant_spectrum", "index_test", "reference_standard", "flow_timing", "validation_split"),
        ("calibration", "discrimination", "threshold_selection", "optimism_correction", "external_validation"),
    ),
    RubricProfile.COMPUTATIONAL: RubricDefinition(
        RubricProfile.COMPUTATIONAL,
        "Relevant EQUATOR/field reporting guideline",
        "Task- and domain-specific methodological appraisal",
        ("dataset_provenance", "leakage", "baseline_choice", "ablation", "external_validity"),
        ("test_set_isolation", "hyperparameter_search", "uncertainty", "multiple_benchmarks", "replication_artifacts"),
    ),
    RubricProfile.COMMON_CORE: RubricDefinition(
        RubricProfile.COMMON_CORE,
        "Relevant EQUATOR guideline when identifiable",
        "No specialist appraisal profile selected",
        ("question_clarity", "argument_structure", "source_relevance"),
        ("quantitative_claims", "uncertainty", "internal_consistency"),
    ),
}


def rubric_items(
    profile: RubricProfile, module_key: str, default_items: list[str] | tuple[str, ...]
) -> tuple[str, ...]:
    rubric = RUBRICS[profile]
    if module_key == "design":
        return rubric.design_items
    if module_key == "statistics":
        return rubric.statistics_items
    return tuple(default_items)


def rubric_prompt(profile: RubricProfile) -> str:
    rubric = RUBRICS[profile]
    calibration = (
        " For systematic reviews, distinguish transparent reporting from risk: material outcome "
        "imputation and shared heterogeneity assumptions still require an impact assessment, even "
        "when the method is described or conventional. Imputing a substantial fraction of response "
        "outcomes, such as 17.7%, warrants at least minor concern even when the method is described "
        "as validated; transparency alone is not methodological reassurance. Explicit exclusion "
        "of clinically relevant "
        "populations, including bipolar or treatment-resistant depression, limits generalizability "
        "and warrants concern proportionate to the stated scope even when disclosed transparently."
        if profile == RubricProfile.SYSTEMATIC_REVIEW
        else ""
    )
    return (
        f"Reporting reference: {rubric.reporting_reference}. Appraisal reference: "
        f"{rubric.appraisal_reference}. Design items: {', '.join(rubric.design_items)}. "
        f"Analysis items: {', '.join(rubric.statistics_items)}.{calibration}"
    )
