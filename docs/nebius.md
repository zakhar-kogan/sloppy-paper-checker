# Nebius deployment

FastAPI and Serverless Jobs use the same backend image. The API container starts `sloppy_checker.main:app`; each job starts `python -m sloppy_checker.job`.

## Required resources

1. Managed PostgreSQL reachable from the API and job subnet.
2. Object Storage bucket with an expiry/lifecycle policy matching report retention.
3. Container Registry image containing the backend package and migrations.
4. MysteryBox secret whose payload keys are `SPC_DATABASE_URL`, `SPC_S3_ACCESS_KEY_ID`, `SPC_S3_SECRET_ACCESS_KEY`, and `SPC_NEBIUS_API_KEY`.
5. FastAPI service credentials authorized to create Serverless AI jobs.

Apply `alembic upgrade head` once per release, then start FastAPI with `SPC_ANALYSIS_DISPATCHER=nebius_job` and `SPC_DOCUMENT_STORE=s3`.

The dispatcher uses the Nebius Python SDK `JobServiceClient`. It never invokes a CLI subprocess. Job configuration deliberately excludes canonical paper text, PDF bytes, database URLs, object-storage keys, and Token Factory credentials. Only the UUID analysis ID varies per job.

## Smoke test

1. Open `/?paper=10.1016/S0140-6736(17)32802-7`.
2. Confirm Crossref, Unpaywall, and NCBI provenance are shown independently.
3. Select the preferred published/accepted/submitted PDF or PMC JATS candidate.
4. Start analysis and confirm the API row moves `queued → running → completed`.
5. Inspect the Nebius job configuration and verify secrets are MysteryBox references and no document text is present.
6. Reload `/?analysis=<uuid>` and confirm the report remains accessible to the same session.

A live smoke requires real Nebius project, network, registry, bucket, PostgreSQL, and MysteryBox identifiers and is therefore intentionally not part of ordinary CI.
