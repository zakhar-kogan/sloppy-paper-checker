# Current implementation plan

Status: methodology v2 reverted. Keep the existing v1.3 methodology and the independent product improvements below.

## Report presentation

1. Keep `Key findings & review scope`.
2. Show explicit severity labels.
3. Bold criterion titles, not whole findings.
4. Present coverage separately from findings.
5. Keep severity understandable without color.
6. Label the score `Review score for assessed items` and keep coverage adjacent.

## Homepage copy

- Use `Check a paper's methods against the evidence.`
- Explain that the report contains criterion-level findings, quoted passages, provenance, and coverage gaps.
- State that the review covers the submitted paper, not its cited literature.
- State that the score does not establish misconduct or scientific truth.

## Startup responsiveness

1. Bypass Cloudflare Rocket Loader for the application module.
2. Use staged availability copy, a bounded session timeout, and Retry.
3. Keep examples and public reports usable when the analysis service is unavailable.
4. Use indexed database aggregate queries for quota checks.
5. Measure live startup after deployment.

## Status warnings

- Keep retractions, corrections, and expressions of concern as sourced warnings.
- Do not alter the methodology score because of a status warning.
- Do not integrate third-party journal lists without stable identity, provenance, and permitted access.

## README

- Keep the concise product description and corrected reviewer data flow.
- Keep local development, deployment, privacy, verification, and provider-data-handling instructions.
- Describe the methodology as open, versioned, automated, and under evaluation rather than validated.

## Methodology boundary

- Keep the existing v1.3 methodology. Do not ship the v2 criteria, absence protocol, scoring changes, or methodology-source API.
- Missing or irrelevant evidence remains unassessed; do not infer a defect from silence.
- A future TOML migration, if useful, must be a literal format conversion of the existing methodology with no new criteria or semantic changes.

## Verification

- Run backend and frontend tests, lint, and the production build.
- Inspect homepage and report presentation at desktop and mobile widths.
- Measure live startup only after deployment.
