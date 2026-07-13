# Sloppy Paper Checker

An evidence-linked scientific paper checker: a Chrome side panel, a full report view, and a self-hosted asynchronous analysis backend. It accepts scholarly PDFs, DOIs, and public paper pages; uses typed Agno specialists when a model provider is configured; and always applies deterministic, versioned scoring.

> **Important:** the 0–100 confidence score is a navigation aid. It is not a validated risk-of-bias tool, evidence-certainty grade, peer review, or misconduct detector. A low score does not establish fraud, and a high score does not establish truth.

## What v1 includes

- Manifest V3 side panel, temporary PDF host access, upload fallback, progress, badge score, and a full evidence dossier.
- FastAPI, PostgreSQL, Redis, Celery, GROBID-ready extraction, encrypted provider settings, SSE events, cancellation, and post-analysis source deletion.
- OpenAI-compatible model discovery with Nebius Token Factory as the default provider.
- Typed specialist findings and an independent evidence gate. Substantive findings without a paper quote or external source cannot enter a report.
- Crossref/Retraction Watch and OpenAlex adapters, plus DataCite and DOAJ adapter foundations. Commercial sources are optional operator-supplied connectors.
- Deep rubric routing for randomized, observational, qualitative, systematic review/meta-analysis, diagnostic/prediction, and computational/ML work; common-core coverage for other paper types.

The product reports sourced behaviors and limitations. It does **not** let a model label a person or venue “pseudoscientific” or “predatory,” and it treats repeated coauthorship as descriptive unless directly relevant evidence supports a stronger conclusion.

## Quickstart

Requirements: Docker Compose and Chrome/Chromium 116+.

```bash
cp .env.example .env
# Set SPC_API_TOKEN to a long random value. Set SPC_ENCRYPTION_KEY for stable production encryption.
docker compose up --build
```

Then build and load the extension:

```bash
cd extension
npm ci
npm run build
```

Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select `extension/dist`. Open the extension settings, connect to `http://127.0.0.1:8787`, and enter `SPC_API_TOKEN`. The extension access token is held in Chrome session storage and clears when the browser session ends.

Provider credentials are entered in extension settings but transmitted only to the self-hosted backend, where they are encrypted at rest. `SPC_NEBIUS_API_KEY` overrides the stored key. Sensitive manuscript operators should enable [Nebius Zero Data Retention controls](https://docs.tokenfactory.nebius.com/legal/legal-quick-guide); provider retention may otherwise occur.

## Local development

```bash
cd backend
UV_CACHE_DIR=/tmp/uv-cache uv sync --extra dev
SPC_API_TOKEN=development-only-change-me uv run uvicorn sloppy_checker.main:app --port 8787

cd ../extension
npm ci
npm run build
npm test
```

Backend tests run with `make test`. The committed [OpenAPI schema](./openapi.json) is the source for generated TypeScript API types; regenerate it with `make openapi` and `cd extension && npm run generate:types`.

## Documentation

- [Architecture and trust boundaries](docs/architecture.md)
- [Scoring methodology](docs/scoring.md)
- [Evidence sources and metric semantics](docs/data-sources.md)
- [Limitations and responsible interpretation](docs/limitations.md)
- [Contributing](CONTRIBUTING.md) and [security policy](SECURITY.md)

Licensed under the [MIT License](LICENSE).

