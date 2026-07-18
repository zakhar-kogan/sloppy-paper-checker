# Review scoring methodology v1.3

The displayed composite is the **Review score**. It is always shown for completed analyses, but findings, content scope, and coverage lead the report.

| Module | Weight | Minimum content |
|---|---:|---|
| Study design and risk-of-bias concepts | 30% | Full text |
| Statistical/computational validity | 25% | Full text |
| Claim–evidence alignment | 20% | Abstract |
| Transparency and reporting | 20% | Full text |
| Disclosures and current-paper affiliations | 5% | Metadata |

Design and statistics items are selected from the classified paper profile; computational papers are not evaluated against clinical missing-data or causal-design items. Grades map to `100 / 75 / 35 / 0` for `no_concern / minor_concern / major_concern / critical_concern`. `not_assessed`, failed, discarded, and content-ineligible items do not enter the numeric mean. Each assessed item receives an equal share of its module weight; the final number is normalized over assessed item weight only.

Two denominators remain visible:

- **Available-content coverage:** assessed items divided by items eligible at this paper’s content level.
- **Full-review coverage:** assessed items divided by all standard full-text items.

A report is provisional when full-review coverage is below 70%, a required module failed, or reviewer adjudication did not complete. Ineligible modules are recorded as `ineligible_at_content_level`; failures are `module_failed`; unreviewed output stays `unreviewed`.

Coverage diagnostics remain in the audit section and are deterministic. They are not a probability that a finding is correct. The report leads with grade distribution, assessed-item coverage, and limitations; the normalized composite is labelled a secondary coverage-weighted heuristic.

Only exact-quote-verified `observed` worker evidence can receive an evidence ID. The ID is bound to its module, rubric item, evidence state, and canonical paper span. The reviewer can grade an item only with matching observed evidence. `not_found` and `ambiguous` notes remain visible operational notes but never create a grade; silence is `not_assessed`. An explicit quoted statement such as “data are not available” is observed evidence and can be assessed.

A failed extraction module appears as an execution warning. Partial final output retains a score normalized over valid assessed items while missing items reduce coverage. A valid reviewer result with no evidence-grounded final grades remains an explicitly unassessed, zero-coverage report rather than being rewritten as a reviewer failure or a fabricated zero-quality score. Provider, timeout, and invalid-output failures use the documented provisional fallback.

Retractions, expressions of concern, and corrections are sourced banners and audit facts. They never cap or otherwise alter the Review score. The score is not a truth probability, evidence-certainty grade, or substitute for a validated appraisal performed by trained reviewers.
