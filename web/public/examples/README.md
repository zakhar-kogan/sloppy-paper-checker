# Precomputed example records

This directory contains the fixed ten-paper showcase used by the GitHub Pages build. These reports are examples of product behavior, not a benchmark or validated accuracy evaluation.

- `manifest.json` drives the gallery cards and fixes their order.
- `reports/` contains exact final `AnalysisReport` v1.3 payloads.
- `audits/` records identity/source checks, displayed-quotation checks, warning review, and secret-scan status.
- `ledger/attempts.jsonl` is append-only and retains failed attempts and reruns.

No paper text, PDFs, credentials, cookies, provider request payloads, or raw model transcripts belong in this directory.

## Regeneration

Start the backend locally with the inline dispatcher, then generate one fixed case at a time. PDF cases require `npm ci` in `web/` so the release-only PDF.js normalizer is available.

```bash
uv run --project backend python scripts/generate_showcase.py attention-2017
```

After every fixed case has a completed attempt, review all warnings and quotation mismatches, then finalize and validate the public records:

```bash
uv run --project backend python scripts/finalize_showcase.py
uv run --project backend python scripts/validate_showcase.py
```

Do not run `finalize_showcase.py` until quotation mismatches are zero and warnings have been reviewed. It marks those completed checks in each audit file and fails if a secret-like value is detected.
