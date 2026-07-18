# Sloppy Paper Checker

[![CI](https://github.com/zakhar-kogan/sloppy-paper-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/zakhar-kogan/sloppy-paper-checker/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-1646e8.svg)](LICENSE)

A web-first, evidence-linked scientific paper review service. Give it a DOI, PMID, PMCID, arXiv record, scholarly URL, or local PDF. It returns a content-aware methodology review with quoted evidence, explicit assessment gaps, source provenance, and an auditable scoring record.

The Review score is a navigation aid, not the probability that a paper is true, peer review, or a misconduct verdict. Content level, coverage, exact quotations, and limitations remain visible in every report.

## Why this project exists

Automated paper summaries often blur together what a paper says, what a model inferred, and what could not be checked. Sloppy Paper Checker keeps those boundaries visible:

- Workers retrieve item-specific evidence but do not assign grades.
- A final reviewer assigns grades against a versioned methodology.
- Exact quotations are checked against the normalized paper before they become evidence.
- Abstract-only and failed modules remain visibly unassessed instead of becoming fabricated zeroes.
- Every report records methodology, parser, model, coverage, token usage, and source context.

For a representative full-text smoke test, use `10.1016/S0140-6736(17)32802-7`. The expected flow is resolution, source preflight, local PDF parsing or PMC JATS normalization, per-module progress, final adjudication, and a reload-safe report URL.

## Architecture

- FastAPI is always the control-plane backend.
- Agno `Agent` with `OpenAILike` runs Token Factory workers and the final reviewer.
- Crossref resolves publication identity; Unpaywall discovers versioned open-access PDFs; NCBI resolves PMID/PMCID and PMC JATS.
- PDF.js parses local and relayed PDFs in the browser. FastAPI normalizes PMC JATS.
- SQLAlchemy supports SQLite locally and PostgreSQL in deployment.
- Canonical documents use a filesystem store locally and S3-compatible Nebius Object Storage in deployment.
- Analysis dispatch is independently selectable: `inline` locally or a Nebius Serverless Job containing only the analysis ID.
- The Chrome extension only detects the current DOI/URL and opens the web UI with `?paper=`. Set `VITE_WEB_APP_URL=https://your-host/` for a deployment build; development defaults to `http://127.0.0.1:5173/`.

There is no Celery, Redis, Beat, GROBID, backend PDF extraction, legacy upload API, or SSE path.

## Data handling

PDF.js extracts PDF text in the browser. The normalized text, not the original local PDF bytes, is sent to the FastAPI service and its configured model provider for review. Do not submit confidential or unpublished material to a deployment whose operator and model-provider policy you do not trust.

Completed analyses delete their canonical stored document after the report is written. Public deployments must also configure Object Storage lifecycle expiry and terminal-path cleanup for failed or cancelled analyses. Guest report access is owner-scoped and expires with the anonymous session.

## Local development

```bash
cp .env.example .env

env UV_CACHE_DIR=/tmp/uv-cache \
  SPC_ENV=development \
  SPC_DATABASE_URL=sqlite:///./paper_checker.db \
  SPC_DOCUMENT_STORE=filesystem \
  SPC_DOCUMENT_STORE_PATH=./data/documents \
  SPC_ANALYSIS_DISPATCHER=inline \
  uv run --project backend --env-file .env \
  uvicorn sloppy_checker.main:app --app-dir backend/src --host 127.0.0.1 --port 8787

npm --prefix web ci
npm --prefix web run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. URLs are reload-safe: `?paper=...` reopens resolution and `?analysis=...` resumes status polling or displays the completed report.

The intake has one explicit `Analyze paper` action. Identifier and URL inputs are resolved automatically as a source preflight, so ranked source candidates can still be inspected or changed before analysis. Running reports expose durable per-module progress, bounded unreviewed extraction notes with verified quote previews, and cancellation; the final reviewer has a configurable total deadline through `SPC_REVIEWER_DEADLINE_SECONDS` (240 seconds by default). Total guest-run quotas are disabled unless `SPC_HOSTED_RUNS_PER_SESSION` is explicitly configured, while the concurrent-run guard remains enabled.

The Docker Compose stack is a small single-host deployment: Caddy, the static web container, FastAPI with inline execution, and PostgreSQL. Run `docker compose up --build` after configuring `.env`.

## Deployment shapes

The repository currently supports two runtime shapes:

1. **Single host:** Caddy serves the web UI and proxies FastAPI on one origin. Docker Compose supplies PostgreSQL and local document storage.
2. **Nebius:** FastAPI is the control plane, Managed PostgreSQL stores lifecycle and report state, Object Storage holds canonical documents, and each analysis runs as a Serverless AI Job.

GitHub Pages can host the static web bundle, but it cannot run FastAPI or protect model credentials. The current frontend uses same-origin `/v1` requests, so a Pages deployment still needs a configurable public API base URL, HTTPS, CORS, and a cross-origin-safe guest-session design. Only the public API URL may be compiled into the Pages bundle. Database, storage, Nebius, and model credentials must remain in MysteryBox or another server-side secret store.

## Nebius deployment

Set PostgreSQL, S3, and job dispatch independently:

```env
SPC_DATABASE_URL=postgresql+psycopg://...
SPC_DOCUMENT_STORE=s3
SPC_S3_ENDPOINT_URL=https://storage.eu-north1.nebius.cloud
SPC_S3_REGION=eu-north1
SPC_S3_BUCKET=paper-documents
SPC_ANALYSIS_DISPATCHER=nebius_job
SPC_NEBIUS_PROJECT_ID=project-...
SPC_NEBIUS_JOB_IMAGE=cr.eu-north1.nebius.cloud/.../sloppy-paper-checker:...
SPC_NEBIUS_JOB_SECRET_ID=mbsec-...
```

Run `alembic upgrade head` before starting FastAPI. The MysteryBox secret must expose `SPC_DATABASE_URL`, `SPC_S3_ACCESS_KEY_ID`, `SPC_S3_SECRET_ACCESS_KEY`, and `SPC_NEBIUS_API_KEY`. The job receives those values as secret references; its only per-analysis plaintext input is `SPC_ANALYSIS_ID`.

Optional analysis telemetry uses OTLP/HTTP and is disabled by default. Set
`SPC_OBSERVABILITY_ENABLED=true`, `SPC_OTEL_EXPORTER_OTLP_ENDPOINT`, and optionally
`SPC_OTEL_EXPORTER_OTLP_HEADERS`. Traces contain stage/model identifiers, timings,
token counts, coverage, and outcomes only; paper text, prompts, quotes, raw model
responses, credentials, and source URLs are never attached.

See [Nebius deployment](docs/nebius.md) and [architecture](docs/architecture.md).

## Verification

```bash
make test
make lint
make build
make openapi
```

The generated OpenAPI schema and web TypeScript types are committed. PostgreSQL contract tests run when `SPC_TEST_POSTGRES_URL` is available; SQLite tests always run.
