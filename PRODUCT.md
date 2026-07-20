# Product

## Register

product

## Users

Sloppy Paper Checker serves researchers, peer reviewers, editors, and evidence-conscious readers who need to assess a scientific paper's methodology without losing sight of the evidence behind each judgment. They may be screening a paper quickly or conducting a closer review, and need dense methodological information to remain traceable and navigable.

## Product Purpose

Sloppy Paper Checker reviews the methodology of a scientific paper against explicit criteria and presents the evidence, provenance, coverage gaps, uncertainty, and limitations behind its assessments. Success means helping people inspect how a conclusion was reached rather than asking them to trust an opaque score. Scores support navigation and prioritization; they do not establish certainty, misconduct, or scientific truth.

## Brand Personality

Rigorous, skeptical, opinionated. The product should feel intellectually serious and candid about limitations while retaining a distinct point of view.

## Anti-references

Avoid generic AI dashboards, soft SaaS styling, decorative AI imagery, and opaque scoring. Do not hide uncertainty behind polished summaries or make consequential research assessment feel playful, clinical, or institutionally bland.

## Design Principles

- Put evidence before verdicts.
- Expose uncertainty, unavailable checks, and coverage limitations.
- Remain visually distinctive without trivializing research assessment.
- Keep dense reports navigable and preserve the path from judgment to source.
- Treat scores as navigation aids, never as substitutes for interpretation.

## Accessibility & Inclusion

Target WCAG 2.2 AA contrast. Support complete keyboard operation, visible focus states, semantic structure, and reduced-motion preferences. Never rely on color alone to communicate an assessment, state, or severity.

## Current Product Contract

- Accept a DOI, PMID, PMCID, arXiv record, scholarly URL, or PDF.
- Resolve source candidates before analysis and preserve their provenance.
- Keep reports private to the submitting browser by default.
- Allow explicit automatic publication for 30 days, later publication, and early unpublication.
- Reuse an exactly compatible private report only within its owner session.
- Reuse an exactly compatible public report across visitors only while it is published and has a public scholarly identifier.
- Keep the source PDF available for report viewing only in the current browser tab; never publish it with the report.
- Keep incomplete, failed, and unassessed methodology checks visible instead of manufacturing certainty.

## Methodology Evolution

The methodology, worker prompt, reviewer prompt, parser, scoring version, provider profile, and model identifiers are recorded with every report. Any methodology or prompt change produces a different compatibility hash, so historical reports remain readable without being presented as equivalent to a new analysis. Iteration should prefer explicit version changes and regression evaluation over silent prompt replacement.

## Public-Beta Operations

Hosted inference has a configurable per-browser allowance, a hidden global safety cap, a per-browser concurrency limit, and an operator kill switch. Visitors should see their own availability and a plain temporary-capacity message, never the exact global remaining budget. Existing compatible reports remain usable when new inference is unavailable.

These controls limit ordinary use; anonymous browser identity is not an abuse-proof billing boundary. Provider-side spending controls and operator monitoring remain the final financial safeguards.

## Deployment Direction

The product contract does not depend on one hosting topology. The current live service favors a small, understandable Compose deployment. The static frontend can move independently to a CDN, and analyses can be dispatched to serverless jobs without changing report URLs or schemas. A fully scale-to-zero API remains an option if traffic justifies new session and persistence adapters; it is not a current product requirement.
