# Nebius deployment

FastAPI and Serverless Jobs use the same backend image. The API container starts `sloppy_checker.main:app`; each job starts `python -m sloppy_checker.job`.

## Web and API topology

Nebius Serverless AI Jobs are background workloads and do not expose a public request URL. The FastAPI control plane must therefore run separately. A Serverless AI Endpoint can run the FastAPI container and expose its port, but it is a long-running container over a VM rather than a browser-function or automatic scale-to-zero service.

The compiled Preact frontend may be served by the included Caddy image or another HTTPS-capable static host. The browser bundle may contain a public API base URL, never `SPC_API_TOKEN`, model-provider credentials, PostgreSQL credentials, Object Storage keys, a Nebius API key, or an endpoint authentication token.

The current anonymous session is an HttpOnly, SameSite=Lax cookie designed for a same-origin deployment. Keep the frontend and `/v1/*` API behind one public hostname; the included Caddy configuration does this and preserves the current security model. A cross-site frontend/API split would require a deliberate replacement session design and is not supported by the current deployment.

## Persistence

Saving is supported even though analysis jobs are ephemeral:

- Managed PostgreSQL stores sessions, resolutions, analysis lifecycle, progress, and final reports.
- Object Storage stores the canonical `PaperDocument` while a job is running.
- MysteryBox supplies database, storage, and Nebius credentials to containers without placing them in job arguments or frontend assets.
- The job receives only the analysis UUID, loads its document from durable storage, and writes progress and results back to PostgreSQL.

The job's boot disk is disposable. Do not use SQLite or the container filesystem for a Serverless Job deployment. Configure an Object Storage lifecycle rule and application cleanup so documents from successful, failed, and cancelled analyses follow the promised retention period.

## Required resources

1. Managed PostgreSQL reachable from the API and job subnet.
2. Object Storage bucket with an expiry/lifecycle policy matching report retention.
3. Container Registry image containing the backend package and migrations.
4. MysteryBox secret whose payload keys are `SPC_DATABASE_URL`, `SPC_S3_ACCESS_KEY_ID`, `SPC_S3_SECRET_ACCESS_KEY`, and the configured provider credential. Use `SPC_PROVIDER_API_KEY` for generic provider configuration or `SPC_NEBIUS_API_KEY` for the backward-compatible Token Factory configuration.
5. FastAPI service credentials authorized to create Serverless AI jobs.

Apply `alembic upgrade head` once per release, then start FastAPI with `SPC_ANALYSIS_DISPATCHER=nebius_job` and `SPC_DOCUMENT_STORE=s3`.

The dispatcher uses the Nebius Python SDK `JobServiceClient`. It never invokes a CLI subprocess. Job configuration deliberately excludes canonical paper text, PDF bytes, database URLs, object-storage keys, and provider credentials. The selected provider profile, base URL, and model IDs are passed as non-secret configuration; only the UUID analysis ID varies per job.

## Smoke test

1. Open `/?paper=10.1016/S0140-6736(17)32802-7`.
2. Confirm Crossref, Unpaywall, and NCBI provenance are shown independently.
3. Select the preferred published/accepted/submitted PDF or PMC JATS candidate.
4. Start analysis and confirm the API row moves `queued → running → completed`.
5. Inspect the Nebius job configuration and verify secrets are MysteryBox references and no document text is present.
6. Reload `/?analysis=<uuid>` and confirm the report remains accessible to the same session.

A live smoke requires real Nebius project, network, registry, bucket, PostgreSQL, and MysteryBox identifiers and is therefore intentionally not part of ordinary CI.
