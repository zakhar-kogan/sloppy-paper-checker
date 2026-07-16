# Sloppy Paper Checker

A web-first, evidence-linked paper review service. Resolve a DOI, PMID, PMCID, arXiv record, scholarly URL, or choose a local PDF; the UI produces a canonical `PaperDocument` and submits it for a content-aware methodology review.

The Review score is a navigation aid, not the probability that a paper is true, peer review, or a misconduct verdict. Content level, coverage, exact quotations, and limitations remain visible in every report.

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

The Docker Compose stack is a small single-host deployment: Caddy, the static web container, FastAPI with inline execution, and PostgreSQL. Run `docker compose up --build` after configuring `.env`.

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

See [Nebius deployment](docs/nebius.md) and [architecture](docs/architecture.md).

## Verification

```bash
make test
make lint
make build
make openapi
```

The generated OpenAPI schema and web TypeScript types are committed. PostgreSQL contract tests run when `SPC_TEST_POSTGRES_URL` is available; SQLite tests always run.
