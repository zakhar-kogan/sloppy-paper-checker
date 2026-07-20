# Deployment

Sloppy Paper Checker is one application with two runtime images:

- `web/Dockerfile` builds the Preact frontend and serves it with Caddy. Caddy owns public HTTPS/static files and proxies `/v1/*` and `/healthz` to the API.
- `backend/Dockerfile` runs FastAPI and the analysis engine.

Persistent state consists of a SQL database and temporary canonical `PaperDocument` objects. The source PDF is parsed and retained only in the browser tab.

## Supported storage profiles

| Profile | Database | Documents | Dispatcher | Intended use |
| --- | --- | --- | --- | --- |
| Local | SQLite | Filesystem | Inline | Tests and one-process development |
| Production | PostgreSQL | Filesystem | Inline | Current single-host deployment |
| Distributed | PostgreSQL | S3-compatible object storage | Nebius Job | Optional background-job deployment |

SQLite and PostgreSQL are two configurations of the same SQLAlchemy schema, not two production databases. SQLite is deliberately limited to one API process and cannot be combined with the Nebius Job dispatcher. PostgreSQL is the supported production database.

## Required production contract

Every production deployment must provide:

- a public same-origin gateway for the frontend and `/v1/*` API;
- `SPC_ENV=production` so the anonymous-session cookie is Secure;
- a strong random `SPC_API_TOKEN`;
- PostgreSQL through `SPC_DATABASE_URL`;
- persistent filesystem storage or S3-compatible object storage;
- an OpenAI-compatible model-provider key and model IDs;
- operator contact addresses for Unpaywall and NCBI;
- `SPC_ALLOWED_HOSTS` containing the public hostname;
- an `alembic upgrade head` step before the new API starts;
- backups for PostgreSQL and any persistent document volume;
- a successful `/healthz` check and one real browser smoke test.

Never place provider, database, S3, or API credentials in the frontend image or Caddy environment. `SPC_API_UPSTREAM` is a non-secret internal address.

## Docker Compose on one host

This is the canonical and currently deployed topology:

```text
Browser → Caddy/Preact → FastAPI → PostgreSQL
                         └──────→ document volume
```

1. Copy `.env.example` to `.env`.
2. Replace all placeholder credentials and email addresses.
3. Set `SPC_PUBLIC_HOST` and include it in `SPC_ALLOWED_HOSTS`.
4. Set `SPC_DATABASE_URL` to the Compose PostgreSQL service.
5. Start the release:

```bash
docker compose up --build -d
```

The API service runs `alembic upgrade head` before Uvicorn. The one-shot `document-init` service fixes ownership on the persistent document volume and then exits; it is not a long-running service.

Verify:

```bash
curl -fsS https://YOUR_HOST/healthz
docker compose ps
```

Then open the public URL in a clean browser session, resolve a known DOI, run an analysis, reload the report URL, and verify private/public behavior as appropriate. Back up PostgreSQL before every schema migration.

## Railway

Railway can host the existing images without combining Caddy and FastAPI into one container. Create one project with four services:

1. **gateway** — repository root `/web`, Dockerfile deployment, public domain;
2. **api** — repository root `/backend`, Dockerfile deployment, private networking only;
3. **PostgreSQL** — Railway PostgreSQL service;
4. **documents** — Railway S3-compatible Bucket.

Recommended gateway variables:

```text
SPC_PUBLIC_HOST=:8080
SPC_API_UPSTREAM=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}
```

Configure the gateway service to listen on/expose port `8080`. Configure `/healthz` as its deployment healthcheck; Caddy proxies it to the API.

The API start command must bind Railway's injected port and run migrations first:

```bash
sh -c 'alembic upgrade head && uvicorn sloppy_checker.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips="*"'
```

Set API variables using Railway service references rather than copied credentials:

```text
SPC_ENV=production
SPC_DATABASE_URL=${{Postgres.DATABASE_URL}}
SPC_DOCUMENT_STORE=s3
SPC_ANALYSIS_DISPATCHER=inline
```

Map the Bucket's current endpoint, region, bucket, access-key, and secret-key variables to the corresponding `SPC_S3_*` settings. Use the exact names shown in Railway's Bucket **Connect** panel; do not guess or commit them. Set `SPC_ALLOWED_HOSTS` to the gateway's Railway/custom domain because Caddy preserves the original request host.

Railway references:

- [Monorepos](https://docs.railway.com/guides/monorepo)
- [Dockerfiles](https://docs.railway.com/guides/dockerfiles)
- [Private networking](https://docs.railway.com/guides/private-networking)
- [PostgreSQL](https://docs.railway.com/guides/postgresql)
- [Buckets](https://docs.railway.com/guides/buckets)
- [Variables](https://docs.railway.com/guides/variables)
- [Healthchecks](https://docs.railway.com/reference/healthchecks)

A Railway one-click template is not published yet. Create one only after this four-service topology has been deployed and smoke-tested manually; a button that omits migrations, object storage, or same-origin routing would be misleading.

## Fly.io

Fly.io also requires two application deployments rather than one Compose deployment:

1. a public **gateway** app built from `web/Dockerfile`;
2. a private **api** app built from `backend/Dockerfile`;
3. Fly Managed Postgres;
4. Tigris S3-compatible object storage for canonical documents.

Use a unique app name and region rather than committing project-specific `fly.toml` values. The gateway should listen on an internal HTTP port such as `8080`:

```text
SPC_PUBLIC_HOST=:8080
SPC_API_UPSTREAM=http://YOUR_API_APP.internal:8787
```

The API should bind `0.0.0.0:8787`, use the managed PostgreSQL private connection string, and run `alembic upgrade head` as a release command before Machines are replaced. Do not expose PostgreSQL publicly. Configure the Tigris endpoint and credentials through Fly secrets and map them to `SPC_S3_*`.

Typical bootstrap:

```bash
fly auth login
fly launch --no-deploy
fly secrets set SPC_API_TOKEN=... SPC_PROVIDER_API_KEY=...
fly deploy
fly status
fly checks list
```

Fly references:

- [Dockerfile deployment](https://fly.io/docs/languages-and-frameworks/dockerfile/)
- [App configuration](https://fly.io/docs/reference/configuration/)
- [Secrets](https://fly.io/docs/apps/secrets/)
- [Private networking](https://fly.io/docs/networking/private-networking/)
- [Managed Postgres](https://fly.io/docs/mpg/)
- [Tigris object storage](https://fly.io/docs/tigris/)

No Fly configuration is checked in yet because app names, organizations, regions, Postgres attachment, and Tigris credentials are deployment-specific. Add platform files only after a real deployment proves them.

## Nebius Serverless Jobs

The optional distributed analysis path keeps FastAPI as the control plane and dispatches one Nebius Job per analysis. It requires managed PostgreSQL, Nebius Object Storage, a registry image, MysteryBox secrets, and network configuration. It is deferred for the current public beta. See [`docs/nebius.md`](docs/nebius.md) for the resource contract.

## Agent deployment runbook

An agent deploying this repository must follow this order:

1. Read `.env.example`, this document, and the target platform's current official documentation.
2. Inventory existing resources before creating anything.
3. Choose exactly one supported profile; do not invent a mixed topology.
4. Confirm the public hostname, internal API address, PostgreSQL URL source, document store, model provider, retention policy, and spending limits.
5. Keep every secret in the platform secret store or an untracked `.env` file.
6. Back up the production database before migrations.
7. Build the exact checked-out commit; never deploy an uncommitted working tree.
8. Run `alembic upgrade head` once before starting the new API.
9. Verify `/healthz`, service status, migration head, and Caddy routing.
10. Drive the live product in a clean browser: session creation, DOI resolution, analysis submission, polling, report reload, and an error path.
11. Confirm the browser receives Secure/HttpOnly session cookies and no API/model/storage secret appears in frontend assets or job arguments.
12. Record the deployed commit and exact resources changed.

Do not claim a one-click deployment, serverless support, backup, or successful migration unless it was exercised on that platform. Do not replace PostgreSQL with SQLite on a multi-instance or background-job deployment.
