# Scoring methodology v1.0

The report calls the composite a **confidence score**. It is versioned, deterministic, and intended to help readers navigate a paper. It is not an estimate of the probability that conclusions are true.

| Dimension | Weight |
|---|---:|
| Design and reasoning | 25 |
| Analysis and statistics | 20 |
| Claim–evidence alignment | 20 |
| Transparency and reproducibility | 12 |
| Reporting and internal consistency | 8 |
| Venue and record integrity | 6 |
| Relevant author and conflict evidence | 5 |
| Field-normalized journal standing | 4 |

Rubric grades map to `100 / 75 / 35 / 0` for `no_concern / minor_concern / major_concern / critical_concern`. `not_assessed` is excluded. Context with no evidence inherits the assessed paper score, which keeps missing context score-neutral while reducing context coverage. Overall coverage is `85% paper coverage + 15% context coverage`; results below 70% are provisional.

A confirmed retraction caps the composite at 10. An expression of concern caps it at 40. Corrections produce a banner and require version-aware reassessment but do not impose a fixed cap. Both the uncapped score and the reason for any cap remain visible.

EQUATOR guidelines inform reporting-completeness checks. Methodological appraisal is a separate concern informed, where applicable, by RoB 2, ROBINS-I, AMSTAR 2, and current QUADAS-3 concepts. The project does not claim to reproduce those instruments or replace trained assessors.

Every finding records category, rubric item, grade/severity, confidence, cited paper spans, external sources, affected conclusions, counterevidence, limitations, and critic disposition. Unsupported substantive findings fail schema validation or are discarded by the critic.

