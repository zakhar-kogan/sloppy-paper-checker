# Review scoring methodology v1.1

The displayed composite is the **Review score**. It is always shown for completed analyses, but findings, content scope, and coverage lead the report.

| Module | Weight | Minimum content |
|---|---:|---|
| Study design and risk-of-bias concepts | 25% | Full text |
| Statistical/computational validity | 20% | Full text |
| Claim–evidence alignment | 20% | Abstract |
| Transparency and reporting | 20% | Full text |
| Record and venue context | 10% | Metadata |
| Disclosures and current-paper affiliations | 5% | Metadata |

Expected items are fixed by the versioned methodology artifact. Grades map to `100 / 75 / 35 / 0` for `no_concern / minor_concern / major_concern / critical_concern`. `not_assessed`, failed, discarded, and content-ineligible items do not enter the numeric mean. Each assessed item receives an equal share of its module weight; the final number is normalized over assessed item weight only.

Two denominators remain visible:

- **Available-content coverage:** assessed items divided by items eligible at this paper’s content level.
- **Full-review coverage:** assessed items divided by all standard full-text items.

A report is provisional when full-review coverage is below 70%, a required module failed, or reviewer adjudication did not complete. Ineligible modules are recorded as `ineligible_at_content_level`; failures are `module_failed`; unreviewed output stays `unreviewed`.

The separate **analysis confidence** reports how much of the automated run can be relied on operationally. It is deterministic:

`weighted assessment coverage × successful evidence-module coverage × exact quote-grounding rate × 100`

A failed Qwen evidence module does not invalidate DeepSeek's full-paper judgment or depress the Review score; it lowers confidence and appears as an execution warning. Likewise, partial final output retains a score normalized over valid assessed items while missing items reduce confidence. A run with no evidence-grounded final grade fails instead of presenting a fabricated zero-quality score.

Retractions, expressions of concern, and corrections are sourced banners and audit facts. They never cap or otherwise alter the Review score. The score is not a truth probability, evidence-certainty grade, or substitute for a validated appraisal performed by trained reviewers.
