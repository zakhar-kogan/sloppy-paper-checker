# Sloppy Paper Checker

[![CI](https://github.com/zakhar-kogan/sloppy-paper-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/zakhar-kogan/sloppy-paper-checker/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-1646e8.svg)](LICENSE)

Sloppy Paper Checker reviews the methodology of scientific papers and shows the evidence behind each assessment. Give it a DOI, PMID, PMCID, arXiv record, scholarly URL, or PDF, and it produces a structured report with quotations, source provenance, coverage gaps, and an auditable score.

Use the score to navigate the report alongside its evidence, coverage, and limitations.

## What it does

- Resolves paper metadata and available full-text sources through Crossref, Unpaywall, and NCBI.
- Selects tailored review criteria for randomized trials, observational studies, systematic reviews, computational research, and general empirical papers.
- Separates evidence collection from final assessment: specialist workers find relevant passages, then a reviewer grades the applicable criteria.
- Verifies quotations against the normalized paper before including them as evidence.
- Preserves unavailable or failed checks with an explicit unassessed status.
- Records the methodology, parser, models, coverage, token usage, and source context used for each report.

For a representative full-text test, try `10.1016/S0140-6736(17)32802-7`.

## How it works

The web app parses PDFs with PDF.js. PMC JATS documents are normalized by the FastAPI backend. The resulting text and document structure are stored temporarily while Agno workers and a final reviewer analyze the paper using Nebius Token Factory models. Progress is saved in the database and displayed by polling, so reports survive page reloads.

Local development uses SQLite, filesystem storage, and analysis running in the FastAPI process. A production deployment can use PostgreSQL, S3-compatible Nebius Object Storage, and one Nebius Serverless Job per analysis. These choices are configured independently.

The Chrome extension is a lightweight shortcut that detects a DOI or uses the current page URL, then opens that paper in the web app.

See [Architecture and trust boundaries](docs/architecture.md) for the complete data flow.

## Data handling

PDF.js parses local PDFs in the browser and sends the extracted text and document structure to the API. For papers discovered online, the API relays the PDF to the browser for the same parsing step. The backend fetches and normalizes PMC JATS content directly.

The normalized document is deleted after a successful analysis. Deployments should also configure storage lifecycle rules to clean up documents left by failed or cancelled analyses. Reports are scoped to the anonymous browser session that created them, which expires after 24 hours by default.

The configured model provider receives paper content during analysis. Choose a deployment and provider whose data-handling policies are appropriate for the material you submit.

## Local development

Requirements:

- Python 3.12–3.14 and [uv](https://docs.astral.sh/uv/)
- Node.js 22.12 or later and npm
- A Nebius Token Factory API key for model-backed reviews

Create the environment file and set `SPC_NEBIUS_API_KEY`, `SPC_UNPAYWALL_EMAIL`, and `SPC_NCBI_EMAIL`:

```bash
cp .env.example .env
```

Start the API in local mode:

```bash
env UV_CACHE_DIR=/tmp/uv-cache \
  SPC_ENV=development \
  SPC_DATABASE_URL=sqlite:///./paper_checker.db \
  SPC_DOCUMENT_STORE=filesystem \
  SPC_DOCUMENT_STORE_PATH=./data/documents \
  SPC_ANALYSIS_DISPATCHER=inline \
  uv run --project backend --env-file .env \
  uvicorn sloppy_checker.main:app --app-dir backend/src \
  --host 127.0.0.1 --port 8787
```

In another terminal, start the web app:

```bash
npm --prefix web ci
npm --prefix web run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. The API documentation is available at `http://127.0.0.1:8787/docs` in development mode.

## Chrome extension

Build the extension and load `extension/dist` as an unpacked extension in Chrome or Chromium:

```bash
npm --prefix extension ci
npm --prefix extension run build
```

Development builds open `http://127.0.0.1:5173/`. Set `VITE_WEB_APP_URL` while building to point the extension at a deployed web app.

## Docker Compose

The included Compose stack runs Caddy, the web app, FastAPI, and PostgreSQL on a single host. Copy `.env.example` to `.env`, replace the placeholder API token, configure the provider key and contact emails, then run:

```bash
docker compose up --build
```

This deployment uses inline analysis and a persistent local document volume. For a distributed Nebius deployment using Managed PostgreSQL, Object Storage, MysteryBox, and Serverless Jobs, follow the [Nebius deployment guide](docs/nebius.md).

Optional OTLP/HTTP tracing is disabled by default. When enabled, its allowlist is limited to operational metadata such as stages, model identifiers, timings, token counts, coverage, and outcomes.

## Verification

```bash
make test
make lint
make build
make openapi
```

CI runs all three test suites, checks backend and web linting, builds the web app and extension, and verifies that the committed OpenAPI schema is current. PostgreSQL contract tests also run when `SPC_TEST_POSTGRES_URL` is set.

## Documentation

- [Architecture and trust boundaries](docs/architecture.md)
- [Scoring methodology](docs/scoring.md)
- [Evidence sources](docs/data-sources.md)
- [Limitations and responsible interpretation](docs/limitations.md)
- [Nebius deployment](docs/nebius.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

Licensed under the [MIT License](LICENSE).
