# Architecture and trust boundaries

```mermaid
flowchart LR
  U["DOI / PMID / PMCID / URL"] --> R["Crossref + Unpaywall + NCBI"]
  R --> C["Ranked candidates + provenance"]
  C -->|"relayed PDF"| P["Browser PDF.js"]
  L["Local PDF"] --> P
  C -->|"PMC JATS"| J["FastAPI JATS normalizer"]
  P --> D["Canonical PaperDocument"]
  J --> D
  D --> O["Filesystem or Object Storage"]
  O --> A["Analysis row in SQLite or PostgreSQL"]
  A --> I["Inline dispatcher"]
  A --> N["Nebius Job: analysis ID only"]
  I --> G["Agno workers + reviewer"]
  N --> G
  G --> A
  A --> H["Compatibility hash"]
  H --> Q["Owner-private or identifier-gated public reuse"]
  A --> W["Polling UI + owner report URL"]
  A -->|"explicit opt-in"| F["Expiring public feed + public report URL"]
```

Each infrastructure concern is configured independently. There is no generic cloud flag: tests can combine a SQL repository, filesystem/S3 document store, and inline/mocked Nebius dispatcher in any supported arrangement.

The live `papers.teleogenic.com` deployment uses Caddy, a static nginx frontend, FastAPI, and PostgreSQL on one host. Analysis runs inline and canonical documents use a persistent local volume. This is the smallest already-operational production topology; the adapter boundaries below allow individual infrastructure concerns to move without changing the public report contract.

The browser parses local and relayed PDFs; PDF bytes are never posted to the API. PMC JATS is normalized by FastAPI. Only a validated canonical document is stored for analysis. Resolved candidate URLs are represented by opaque IDs cached in SQL with an expiry, so the relay never accepts a caller-supplied destination.

SQLite is deliberately single-process and local. Nebius jobs require PostgreSQL and S3 storage; startup validation rejects SQLite or filesystem storage with `nebius_job`.

The job specification contains the analysis ID, non-secret adapter coordinates, image and model IDs. PostgreSQL, Object Storage, and model-provider credentials are MysteryBox references. The job loads the document by ID, runs the same Agno engine as inline mode, and updates PostgreSQL. FastAPI remains the owner of sessions, resolution, lifecycle access control, and reports.

Agno is intentionally narrow: `Agent` and `OpenAILike` provide structured model calls through an operator-configured OpenAI-compatible API, with Nebius Token Factory as the default. Scheduling, persistence, source resolution, and scoring are ordinary application code. The unused Agno `Workflow` factory was removed.

Analysis progress is stored as safe stage and module events in the existing analysis JSON event field. The polling status contract exposes labels, completion state, evidence-note counts, bounded observations, and short exact-quote previews verified against the canonical document; it never exposes prompts, the complete paper, raw model output, or credentials. Deterministic routing only selects likely chunks; Agno workers interpret evidence and the Agno reviewer assigns judgments. Reviewer execution has a total deadline; timeout or provider failure produces an explicitly provisional, unreviewed report rather than leaving the lifecycle running indefinitely.

Completed analyses record a compatibility digest over the paper hash, content level, source format, rubric profile, scoring version, methodology bundle, parser, provider profile, and model identifiers. A compatible private report is visible only to its owner session. Cross-visitor reuse additionally requires a currently published report and a public DOI, arXiv, PubMed, or PMC identifier. Unpublication or expiry immediately removes cross-visitor reuse eligibility.

Private terminal reports and unreferenced documents expire after 24 hours by default. Explicitly published reports expire after 30 days unless unpublished sooner. The cleanup loop removes expired analyses, stale resolutions, and orphaned stored documents; publishing never publishes the source PDF.

Anonymous sessions use an HttpOnly SameSite cookie; production cookies are Secure. Canonical documents and private reports are owner-scoped; explicitly published reports are readable without owner credentials until unpublication or expiry. Native logs and report audit fields work without additional services. Optional OTLP/HTTP tracing is disabled by default and exports only allowlisted identifiers, model names, timings, token counts, coverage, and outcomes; it is compatible with Langfuse collectors without adding a Langfuse SDK.

Public-beta inference controls are deliberately separate from report access: a per-browser rolling allowance limits ordinary use, a hidden global cap protects operator spending, a concurrency limit prevents overlapping runs, and an emergency switch disables new hosted analysis. The session API exposes personal remaining allowance and only a capacity-available boolean for the global control. Existing compatible reports remain accessible when inference is unavailable.

The static frontend can be deployed independently to a CDN. The existing dispatcher can move analysis execution to Nebius Serverless Jobs with managed PostgreSQL, Object Storage, and MysteryBox while FastAPI remains the control plane. A fully scale-to-zero API would require alternative session and persistence adapters; the current boundaries preserve that option without adding those abstractions before they are needed.
