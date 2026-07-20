import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { ApiError, api } from "./api";
import type {
  AnalysisReport,
  AnalysisStatus,
  DocumentReceipt,
  PaperDocument,
  PublicReportSummary,
  ReusableAnalysis,
  SessionView,
  ResolvedPaper,
} from "./domain";
import { duration, errorMessage, fallbackWarnings, isResolvableInput, orderedCandidates, sourceLabel } from "./intake";
import { parsePdf } from "./pdf";
import { buildAssessmentGroups, coverageStateLabel, findingDisplayTitle, moduleStateLabel } from "./report";
import {
  exampleHref,
  exampleIdFromSearch,
  fetchExampleManifest,
  fetchExampleReport,
  type ExampleManifest,
} from "./showcase";

type Phase = "input" | "resolving" | "resolved" | "preparing" | "running" | "report";
type InputMode = "identifier" | "upload";
type ReportOrigin = "live" | "example" | "public";
type Visibility = "private" | "public";

const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const percent = (value: number) => `${Math.round(value * 100)}%`;
const words = (value: string) => value.replaceAll("_", " ");
const terminalStates = new Set(["completed", "failed", "skipped"]);

async function hashText(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

function canonicalPaperUrl(identity: AnalysisReport["identity"]): string | null {
  if (identity.doi) return `https://doi.org/${identity.doi}`;
  if (identity.arxiv_id) return `https://arxiv.org/abs/${identity.arxiv_id}`;
  if (identity.pmcid) return `https://pmc.ncbi.nlm.nih.gov/articles/${identity.pmcid}/`;
  if (identity.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${identity.pmid}/`;
  return null;
}

function paperLinks(report: AnalysisReport): { label: string; href: string }[] {
  const links: { label: string; href: string }[] = [];
  if (report.identity.doi) links.push({ label: `DOI ${report.identity.doi}`, href: `https://doi.org/${report.identity.doi}` });
  if (report.identity.arxiv_id) links.push({ label: `arXiv ${report.identity.arxiv_id}`, href: `https://arxiv.org/abs/${report.identity.arxiv_id}` });
  if (report.identity.pmid) links.push({ label: `PubMed ${report.identity.pmid}`, href: `https://pubmed.ncbi.nlm.nih.gov/${report.identity.pmid}/` });
  if (report.identity.pmcid) links.push({ label: `PMC ${report.identity.pmcid}`, href: `https://pmc.ncbi.nlm.nih.gov/articles/${report.identity.pmcid}/` });
  if (report.source_url && !links.some((link) => link.href === report.source_url)) {
    links.push({ label: report.source_provider ? `Analyzed source · ${report.source_provider}` : "Analyzed source", href: report.source_url });
  }
  return links;
}

async function metadataDocument(resolution: ResolvedPaper, failedCandidateIds: string[] = []): Promise<PaperDocument> {
  const identity = resolution.identity;
  const metadata = [
    identity.title,
    identity.authors?.length ? `Authors: ${identity.authors.join(", ")}` : "",
    identity.journal ? `Venue: ${identity.journal}` : "",
    identity.doi ? `DOI: ${identity.doi}` : "",
    identity.arxiv_id ? `arXiv: ${identity.arxiv_id}` : "",
    identity.pmid ? `PMID: ${identity.pmid}` : "",
    identity.pmcid ? `PMCID: ${identity.pmcid}` : "",
    resolution.abstract ? `Abstract\n${resolution.abstract}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
  if (!metadata.trim()) throw new Error("The resolver found no analyzable metadata or abstract.");
  const sourceUrl = canonicalPaperUrl(identity);
  return {
    schema_version: "1.0",
    identity,
    content_level: resolution.abstract ? "abstract" : "metadata",
    source_format: resolution.abstract ? "abstract" : "metadata",
    sha256: await hashText(metadata),
    parser_name: "resolver-metadata",
    parser_version: "1.0",
    source_url: sourceUrl ?? undefined,
    source_provider: sourceUrl ? "Canonical record" : undefined,
    text: metadata,
    pages: [],
    sections: resolution.abstract
      ? [{ id: "abstract", title: "Abstract", start: metadata.indexOf("Abstract"), end: metadata.length }]
      : [],
    spans: [{ id: "metadata", text: metadata, start: 0, end: metadata.length }],
    references: [],
    extraction_warnings: [...(resolution.limitations ?? []), ...fallbackWarnings(resolution, failedCandidateIds)],
  };
}

function Header({ onReset }: { onReset: () => void }) {
  return (
    <header class="masthead">
      <button class="brand" type="button" onClick={onReset} aria-label="Start a new review">
        <span class="brand-mark">SPC</span>
        <span>Sloppy Paper Checker</span>
      </button>
      <p class="masthead-note">Evidence-linked review · methodology v1</p>
    </header>
  );
}

function ScopeNote() {
  return (
    <aside class="scope-note">
      <span class="eyebrow">What this does</span>
      <p>
        Reviews the paper you provide against explicit methodology items. It does not retrieve cited papers,
        establish misconduct, or turn a score into certainty.
      </p>
    </aside>
  );
}

function ResolutionCard({
  resolution,
  selected,
  onSelect,
}: {
  resolution: ResolvedPaper;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const candidates = resolution.candidates ?? [];
  const selectedCandidate = candidates.find((candidate) => candidate.id === selected);
  return (
    <section class="resolution-card" aria-live="polite">
      <div>
        <span class="eyebrow">Resolved record</span>
        <h2>{resolution.identity.title || "Untitled scholarly record"}</h2>
        <p class="byline">
          {resolution.identity.authors?.slice(0, 4).join(", ") || "Authors unavailable"}
          {(resolution.identity.authors?.length || 0) > 4 ? " et al." : ""}
        </p>
      </div>
      <dl class="identity-grid">
        <div><dt>Identity</dt><dd>{resolution.identity.doi || resolution.identity.arxiv_id || resolution.identity.pmcid || resolution.identity.pmid || "metadata only"}</dd></div>
        <div><dt>Preferred candidate</dt><dd>{selectedCandidate ? `${selectedCandidate.format.toUpperCase()} · ${selectedCandidate.provider}` : words(resolution.content_level)}</dd></div>
        <div><dt>Resulting scope</dt><dd><span class={`level level-${resolution.content_level}`}>{words(resolution.content_level)}</span></dd></div>
      </dl>
      <div class="provenance-list" aria-label="Resolution provenance">
        {(resolution.provenance ?? []).map((record) => (
          <span class={record.available ? "available" : "unavailable"} key={record.provider} title={record.detail || "Available"}>
            {record.provider} · {record.available ? "available" : "unavailable"}
          </span>
        ))}
      </div>
      {candidates.length > 1 && (
        <label class="field compact-field">
          <span>Source version</span>
          <select value={selected} onChange={(event) => onSelect(event.currentTarget.value)}>
            {candidates.map((candidate) => (
              <option value={candidate.id} key={candidate.id}>
                {candidate.format.toUpperCase()} · {candidate.provider} · {candidate.version || "version unspecified"}
              </option>
            ))}
          </select>
        </label>
      )}
      {(resolution.limitations?.length ?? 0) > 0 && <p class="quiet">{resolution.limitations?.join(" ")}</p>}
    </section>
  );
}
function VisibilityControl({
  value,
  onChange,
}: {
  value: Visibility;
  onChange: (value: Visibility) => void;
}) {
  return (
    <fieldset class="visibility-control">
      <legend>Report visibility</legend>
      <div class="visibility-options">
        <label class={value === "private" ? "selected" : ""}>
          <input type="radio" name="visibility" value="private" checked={value === "private"} onChange={() => onChange("private")} />
          <span><strong>Private</strong><small>Available to this browser for 24 hours</small></span>
        </label>
        <label class={value === "public" ? "selected" : ""}>
          <input type="radio" name="visibility" value="public" checked={value === "public"} onChange={() => onChange("public")} />
          <span><strong>Public</strong><small>Published automatically for 30 days</small></span>
        </label>
      </div>
      <p>{value === "private"
        ? "The report will not enter the public feed."
        : "The completed report—including findings, quotations, provenance, and model metadata—will be publicly accessible. The PDF itself is not published."}</p>
    </fieldset>
  );
}


function ReusePrompt({
  match,
  freshAvailable,
  onReuse,
  onFresh,
}: {
  match: ReusableAnalysis;
  freshAvailable: boolean;
  onReuse: () => void;
  onFresh: () => void;
}) {
  return (
    <section class="reuse-prompt" role="dialog" aria-labelledby="reuse-title" aria-describedby="reuse-description">
      <span class="eyebrow">{match.access === "public" ? "Compatible public review found" : "Your existing review found"}</span>
      <h2 id="reuse-title">This exact analysis already exists.</h2>
      <p id="reuse-description">
        Generated {new Date(match.completed_at).toLocaleDateString()} with methodology {match.methodology_version},
        {" "}{words(match.content_level)} content, and {percent(match.coverage)} coverage.
      </p>
      <dl>
        <div><dt>Score</dt><dd>{Math.round(match.review_score)}/100</dd></div>
        <div><dt>Source</dt><dd>{match.source_format.toUpperCase()}</dd></div>
        <div><dt>Worker</dt><dd>{match.worker_model || "Deterministic fallback"}</dd></div>
        <div><dt>Reviewer</dt><dd>{match.reviewer_model || "Not run"}</dd></div>
      </dl>
      <div class="reuse-actions">
        <button class="analyze-button" type="button" onClick={onReuse}>Open existing review <span>→</span></button>
        <button class="secondary-button" type="button" disabled={!freshAvailable} onClick={onFresh}>Run fresh analysis</button>
      </div>
      {!freshAvailable && <small>Fresh inference capacity is exhausted; the existing review remains available.</small>}
    </section>
  );
}



function Progress({
  status,
  localStage,
  cancelling,
  onCancel,
}: {
  status: AnalysisStatus | null;
  localStage: string;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const progress = status?.progress || (localStage ? 12 : 0);
  const stageStarted = status?.stage_started_at ? new Date(status.stage_started_at).getTime() : clock;
  const elapsed = Math.max(0, Math.floor((clock - stageStarted) / 1000));
  const moduleMap = new Map<string, NonNullable<AnalysisStatus["events"]>[number]>();
  (status?.events ?? []).filter((event) => event.kind === "module" && event.key).forEach((event) => moduleMap.set(event.key!, event));
  const modules = [...moduleMap.values()];
  const finishedModules = modules.filter((event) => terminalStates.has(event.state)).length;
  const evidenceCount = modules.reduce((total, event) => total + event.evidence_count, 0);
  const awaitingReviewer = Boolean(status && status.state === "running" && progress >= 84);
  return (
    <main class="progress-page">
      <span class="eyebrow">Review in progress</span>
      <h1>{cancelling ? "Cancelling review" : status?.stage || localStage}</h1>
      <div class={`progress-track ${awaitingReviewer ? "indeterminate" : ""}`} aria-label="Analysis progress"><span style={{ width: `${progress}%` }} /></div>
      <div class="progress-facts">
        <div><span>Current operation</span><strong>{duration(elapsed)}</strong></div>
        <div><span>Methodology categories</span><strong>{modules.length ? `${finishedModules} / ${modules.length}` : "Preparing"}</strong></div>
        <div><span>Evidence notes collected</span><strong>{evidenceCount || "—"}</strong></div>
      </div>
      {modules.length > 0 && (
        <ol class="module-progress" aria-label="Methodology category progress">
          {modules.map((event) => (
            <li class={`module-${event.state}`} key={event.key}>
              <span class="module-dot" aria-hidden="true" />
              <div>
                <strong>{event.label}</strong>
                <small>{event.state === "completed" ? `${event.evidence_count} evidence note${event.evidence_count === 1 ? "" : "s"}` : event.detail || words(event.state)}</small>
                {(event.notes?.length ?? 0) > 0 && (
                  <details class="evidence-notes">
                    <summary>Unreviewed extraction notes</summary>
                    <ul>{event.notes?.map((note) => (
                      <li key={`${event.key}-${note.rubric_item}`}>
                        <strong>{words(note.rubric_item)}</strong>
                        <small>{words(note.evidence_state ?? "ambiguous")}</small>
                        <p>{note.observation}</p>
                        {note.quotes?.map((quote, index) => <blockquote key={`${note.rubric_item}-${index}`}>“{quote}”</blockquote>)}
                      </li>
                    ))}</ul>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {elapsed >= 45 && status?.state === "running" && (
        <p class="slow-note"><strong>This step is taking longer than usual.</strong> The reviewer stage has a four-minute total deadline, including validation and one possible schema repair.</p>
      )}
      <div class="progress-actions">
        <p class="quiet">Updates are polled from the backend. Category evidence is operational progress, not a provisional grade.</p>
        {status && status.state === "running" && <button class="secondary-button" type="button" disabled={cancelling} onClick={onCancel}>{cancelling ? "Cancelling…" : "Cancel review"}</button>}
      </div>
    </main>
  );
}

function Report({
  report,
  onReset,
  origin,
  viewerUrl,
}: {
  report: AnalysisReport;
  onReset: () => void;
  origin: ReportOrigin;
  viewerUrl?: string;
}) {
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [publication, setPublication] = useState<PublicReportSummary | null>(null);
  const [publishError, setPublishError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const links = paperLinks(report);
  useEffect(() => {
    if (origin !== "live") return;
    void api.publication(report.id)
      .then(setPublication)
      .catch((caught) => setPublishError(errorMessage(caught)));
  }, [origin, report.id]);
  const title = report.identity.title || report.identity.doi || "Paper review";
  const findings = report.findings.filter((finding) => finding.critic_disposition !== "discarded");
  const assessmentGroups = buildAssessmentGroups(report);
  const hasFinalScore = (report.assessed_item_count ?? 0) > 0;
  const hasEvidenceNotes = (report.evidence_notes?.length ?? 0) > 0;
  const gradeCounts = findings.reduce<Record<string, number>>((counts, finding) => {
    if (finding.grade !== "not_assessed") counts[finding.grade] = (counts[finding.grade] ?? 0) + 1;
    return counts;
  }, {});
  return (
    <main class="report-page">
      {origin === "example" && (
        <div class="precomputed-banner">
          <span>Precomputed example</span>
          <p>This is a fixed demonstration report, not a live analysis or validated accuracy result.</p>
        </div>
      )}
      <section class="report-title">
        <span class="eyebrow">Automated evidence review</span>
        <h1>{title}</h1>
        <p>{report.identity.authors?.slice(0, 5).join(", ")}</p>
        {links.length > 0 && (
          <nav class="paper-links" aria-label="Paper identifiers and sources">
            {links.map((link) => <a href={link.href} target="_blank" rel="noreferrer" key={link.href}>{link.label} <span aria-hidden="true">↗</span></a>)}
          </nav>
        )}
      </section>
      {viewerUrl && (
        <details class="paper-viewer">
          <summary><span>View analyzed PDF</span><small>Available only in this browser tab</small></summary>
          <iframe src={viewerUrl} title={`PDF of ${title}`} loading="lazy" />
        </details>
      )}

      {report.banners.map((banner) => <div class="record-banner" key={banner}>{banner} {(report.context.record_sources ?? []).map((source) => <a href={source.url} target="_blank" rel="noreferrer" key={source.url}>Source ↗</a>)}</div>)}
      {(report.execution_warnings ?? []).map((warning) => <div class="record-banner" key={warning}>{warning}</div>)}

      <section class="takeaways">
        <div>
          <span class="eyebrow">Read this first</span>
          <h2>Scope & major takeaways</h2>
          <ul>{(report.summary ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div class="score-card">
          <span class="score-label">Methodology review score</span>
          <div class="score-primary">
            <strong>{hasFinalScore ? Math.round(report.review_score) : "—"}</strong>
            {hasFinalScore && <span>/100</span>}
          </div>
          <p class={report.coverage.provisional ? "provisional" : "complete-label"}>{!hasFinalScore ? "Not scored" : report.coverage.provisional ? "Provisional score" : "Completed score"}</p>
          <dl>
            <div><dt>Grounded concerns</dt><dd>{(gradeCounts.critical_concern ?? 0) + (gradeCounts.major_concern ?? 0) + (gradeCounts.minor_concern ?? 0)}</dd></div>
            <div><dt>No concern</dt><dd>{gradeCounts.no_concern ?? 0}</dd></div>
            <div><dt>Items assessed</dt><dd>{report.assessed_item_count ?? report.findings.filter((item) => item.grade !== "not_assessed").length}</dd></div>
            <div><dt>Content</dt><dd>{words(report.content_level)}</dd></div>
            <div><dt>Full-review coverage</dt><dd>{percent(report.coverage.full_review)}<small>{coverageStateLabel(report.coverage.provisional)}</small></dd></div>
            <div><dt>Available-content coverage</dt><dd>{percent(report.coverage.available)}</dd></div>
          </dl>
        </div>
      </section>

      <section class="assessment-section">
        <div class="section-heading"><span class="index">01</span><div><span class="eyebrow">Methodology & findings</span><h2>Assessment by category</h2></div></div>
        <div class="assessment-groups">
          {assessmentGroups.map((group) => (
            <details class="assessment-category" key={group.key} open={group.hasConcern}>
              <summary>
                <div class="category-copy">
                  <h3>{group.label}</h3>
                  <div class="category-meta">
                    <span class={`concern-count ${group.hasConcern ? "has-concerns" : "no-concerns"}`}>
                      {group.concernLabel}
                    </span>
                    <span>{group.assessedItems} of {group.expectedItems} assessed</span>
                    {group.gapCount > 0 && <span>{group.gapCount} assessment gap{group.gapCount === 1 ? "" : "s"}</span>}
                    <span class={`review-state state-${group.state ?? "unknown"}`}>{moduleStateLabel(group.state)}</span>
                  </div>
                  {group.limitation && <small>{group.limitation}</small>}
                </div>
                <div class="category-score"><span>Score</span><strong>{group.score === null ? "—" : `${Math.round(group.score)}/100`}</strong></div>
                <span class="disclosure-mark" aria-hidden="true" />
              </summary>
              <div class="assessment-items">
                {group.items.map((finding) => (
                  <details class={`assessment-item grade-${finding.grade}`} key={finding.id} open={finding.grade === "major_concern" || finding.grade === "critical_concern"}>
                    <summary>
                      <span class="finding-grade">{words(finding.grade)}</span>
                      <strong>{findingDisplayTitle(finding)}</strong>
                      <span class="disclosure-mark" aria-hidden="true" />
                    </summary>
                    <div class="assessment-body">
                      <p>{finding.explanation}</p>
                      {(finding.paper_spans ?? []).map((span, index) => (
                        <blockquote key={`${finding.id}-${index}`}>
                          “{span.quote}”
                          <cite>{span.page ? `Page ${span.page}` : span.section || span.paragraph || "Normalized paper"}</cite>
                        </blockquote>
                      ))}
                      {(finding.limitations?.length ?? 0) > 0 && <p class="quiet">Limitations: {finding.limitations?.join(" ")}</p>}
                    </div>
                  </details>
                ))}
                {group.items.length === 0 && <p class="assessment-empty">No item-level assessments were returned for this category.</p>}
              </div>
            </details>
          ))}
        </div>
      </section>

      {hasEvidenceNotes && (
        <section class="extraction-section">
          <div class="section-heading"><span class="index">02</span><div><span class="eyebrow">Worker evidence</span><h2>Unreviewed extraction notes</h2></div></div>
          <p class="quiet">These are grounded retrieval notes collected before final adjudication. They are not findings or grades.</p>
          <div class="report-evidence-notes">
            {(report.module_statuses ?? []).map((module) => {
              const notes = report.evidence_notes?.filter((note) => note.module_key === module.key) ?? [];
              if (!notes.length) return null;
              return (
                <details key={module.key}>
                  <summary>{module.label} <span>{notes.length} notes</span></summary>
                  <ul>{notes.map((note) => (
                    <li key={`${module.key}-${note.rubric_item}`}>
                      <strong>{words(note.rubric_item)}</strong> <small>{words(note.evidence_state ?? "ambiguous")}</small><p>{note.observation}</p>
                      {note.quotes?.map((quote, index) => <blockquote key={`${note.rubric_item}-${index}`}>“{quote}”</blockquote>)}
                    </li>
                  ))}</ul>
                </details>
              );
            })}
          </div>
        </section>
      )}

      <section class="audit-section">
        <div class="section-heading"><span class="index">{hasEvidenceNotes ? "03" : "02"}</span><div><span class="eyebrow">Reproducibility</span><h2>Provenance & audit</h2></div></div>
        <dl class="audit-grid">
          <div><dt>Methodology</dt><dd>{report.methodology_version}<code>{report.methodology_hash.slice(0, 12)}</code></dd></div>
          <div><dt>Parser</dt><dd>{report.parser_name} {report.parser_version}</dd></div>
          <div><dt>Provider profile</dt><dd>{report.provider_profile}<br />{report.provider_protocol}</dd></div>
          <div><dt>Models</dt><dd>Worker: {report.worker_model || "deterministic fallback"}<br />Reviewer: {report.reviewer_model || "not run"}</dd></div>
          <div><dt>Paper content hash</dt><dd><code>{report.paper_sha256.slice(0, 18)}…</code></dd></div>
          <div><dt>Token usage</dt><dd>{Object.keys(report.token_usage ?? {}).length ? Object.entries(report.token_usage ?? {}).map(([key, value]) => `${key}: ${value}`).join(" · ") : "No model usage recorded"}</dd></div>
          <div><dt>Coverage diagnostics</dt><dd>Assessment: {percent(report.confidence_components?.assessment_coverage ?? 0)}<br />Evidence modules: {percent(report.confidence_components?.evidence_module_coverage ?? 0)}<br />Grounded evidence: {percent(report.confidence_components?.quote_grounding_rate ?? 0)}<br />Source quality: {percent(report.confidence_components?.source_quality ?? 0)}</dd></div>
        </dl>
        <details class="limitations"><summary>Limitations and execution record</summary><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
      </section>

      {origin === "live" && (
        <section class="publish-panel" aria-labelledby="publish-title">
          <div>
            <span class="eyebrow">{publication ? "Public for 30 days" : "Optional public record"}</span>
            <h2 id="publish-title">{publication ? "This review is publicly accessible" : "Share this reviewed paper"}</h2>
            <p>{publication
              ? `It will expire automatically on ${new Date(publication.expires_at).toLocaleDateString()}. You can unpublish it sooner.`
              : "Publishing makes the paper title, findings, quotations, score, source provenance, and model metadata public for 30 days. Your session identifier and PDF are never included."}</p>
          </div>
          {publication ? (
            <div class="publish-result" role="status">
              <a href={`?${new URLSearchParams({ public: publication.slug })}`}>Open public report ↗</a>
              <button
                class="text-button"
                type="button"
                onClick={() => {
                  setPublishing(true);
                  void api.unpublish(report.id)
                    .then(() => setPublication(null))
                    .catch((caught) => setPublishError(errorMessage(caught)))
                    .finally(() => setPublishing(false));
                }}
                disabled={publishing}
              >
                Unpublish
              </button>
            </div>
          ) : (
            <div class="publish-confirm">
              <label>
                <input
                  type="checkbox"
                  checked={publishConfirmed}
                  onChange={(event) => setPublishConfirmed(event.currentTarget.checked)}
                />
                <span>I understand this report and its quoted paper text will be publicly accessible.</span>
              </label>
              <button
                class="secondary-button"
                type="button"
                disabled={!publishConfirmed || publishing}
                onClick={() => {
                  setPublishError("");
                  setPublishing(true);
                  void api.publish(report.id)
                    .then(setPublication)
                    .catch((caught) => setPublishError(errorMessage(caught)))
                    .finally(() => setPublishing(false));
                }}
              >
                {publishing ? "Publishing…" : "Publish report"}
              </button>
            </div>
          )}
          {publishError && <div class="error-box" role="alert">{publishError}</div>}
        </section>
      )}

      {origin === "example"
        ? <a class="secondary-button new-review" href={`${import.meta.env.BASE_URL}#examples`}>Back to examples</a>
        : <button class="secondary-button new-review" type="button" onClick={onReset}>Review another paper</button>}
    </main>
  );
}

function ExampleGallery({
  manifest,
  loading,
  error,
}: {
  manifest: ExampleManifest | null;
  loading: boolean;
  error: string;
}) {
  return (
    <section class="examples-section" id="examples" aria-labelledby="examples-title">
      <div class="examples-heading">
        <div>
          <span class="eyebrow">A fixed field set</span>
          <h2 id="examples-title">Example reviews</h2>
        </div>
        <p>Ten papers across computational, clinical, observational, diagnostic, qualitative, and general empirical research. These are demonstrations, not benchmark results.</p>
      </div>
      {loading && <p class="examples-status" role="status">Loading the example index…</p>}
      {error && <div class="error-box examples-error" role="alert">{error}</div>}
      {manifest && (
        <div class="example-grid">
          {manifest.examples.map((example, index) => (
            <a
              class="example-card"
              href={exampleHref(example.id)}
              key={example.id}
            >
              <div class="example-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</div>
              <div class="example-copy">
                <span class="example-profile">{words(example.profile)}</span>
                <h3>{example.title}</h3>
                <p>{example.identifier}</p>
              </div>
              <dl class="example-facts">
                <div><dt>Year</dt><dd>{example.year}</dd></div>
                <div><dt>Content</dt><dd>{words(example.content_level)}</dd></div>
                <div><dt>Coverage</dt><dd>{percent(example.coverage)}</dd></div>
                <div><dt>Concerns</dt><dd>{example.concern_count}</dd></div>
              </dl>
              <span class="example-open">Read review <span aria-hidden="true">↗</span></span>
            </a>
          ))}
        </div>
      )}
      {manifest && <p class="examples-disclosure">{manifest.disclosure}</p>}
    </section>
  );
}

function PublicFeed({
  reports,
  loading,
  error,
}: {
  reports: PublicReportSummary[];
  loading: boolean;
  error: string;
}) {
  return (
    <section class="public-feed" id="public-reports" aria-labelledby="public-reports-title">
      <div class="examples-heading">
        <div>
          <span class="eyebrow">Published by choice</span>
          <h2 id="public-reports-title">Recently shared reviews</h2>
        </div>
        <p>Reports selected as public appear here for 30 days, then expire automatically. Private reviews remain tied to their submitter’s browser and never enter this list.</p>
      </div>
      {loading && <p class="examples-status" role="status">Loading public reports…</p>}
      {error && <div class="error-box examples-error" role="alert">{error}</div>}
      {!loading && !error && reports.length === 0 && (
        <p class="public-empty">No visitor reports have been published yet. The curated examples below remain available without starting an analysis.</p>
      )}
      {reports.length > 0 && (
        <div class="public-grid">
          {reports.map((item) => (
            <a class="public-card" href={`?${new URLSearchParams({ public: item.slug })}`} key={item.slug}>
              <span class="example-profile">{words(item.profile)}</span>
              <h3>{item.title}</h3>
              <dl class="example-facts">
                <div><dt>Year</dt><dd>{item.year ?? "—"}</dd></div>
                <div><dt>Content</dt><dd>{words(item.content_level)}</dd></div>
                <div><dt>Coverage</dt><dd>{percent(item.coverage)}</dd></div>
                <div><dt>Concerns</dt><dd>{item.concern_count}</dd></div>
              </dl>
              <span class="example-open">Open public review <span aria-hidden="true">↗</span></span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}


export default function App() {
  const [phase, setPhase] = useState<Phase>("input");
  const [mode, setMode] = useState<InputMode>("identifier");
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [reuseMatch, setReuseMatch] = useState<ReusableAnalysis | null>(null);
  const [preparedDocumentId, setPreparedDocumentId] = useState("");
  const [viewerUrl, setViewerUrl] = useState("");
  const [resolution, setResolution] = useState<ResolvedPaper | null>(null);
  const [resolvedQuery, setResolvedQuery] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [reportOrigin, setReportOrigin] = useState<ReportOrigin>("live");
  const [error, setError] = useState("");
  const [localStage, setLocalStage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [exampleManifest, setExampleManifest] = useState<ExampleManifest | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [publicReports, setPublicReports] = useState<PublicReportSummary[]>([]);
  const [publicLoading, setPublicLoading] = useState(true);
  const [publicError, setPublicError] = useState("");
  const [exampleLoading, setExampleLoading] = useState(true);
  const [exampleError, setExampleError] = useState("");
  const queryRef = useRef("");
  const resolutionRequest = useRef<{ value: string; promise: Promise<ResolvedPaper> } | null>(null);
  const viewerUrlRef = useRef("");

  // Bootstrap once; query parameters select durable live, example, or public report routes.
  useEffect(() => {
    const bootstrap = async () => {
      const params = new URLSearchParams(window.location.search);
      const exampleId = exampleIdFromSearch(window.location.search);
      const publicSlug = params.get("public");
      const analysisId = params.get("analysis");
      const paper = params.get("paper");
      const manifestPromise = fetchExampleManifest()
        .then((manifest) => {
          setExampleManifest(manifest);
          return manifest;
        })
        .catch((caught) => {
          setExampleError(errorMessage(caught));
          return null;
        })
        .finally(() => setExampleLoading(false));
      void api.publicReports()
        .then((result) => setPublicReports(result.reports))
        .catch((caught) => setPublicError(errorMessage(caught)))
        .finally(() => setPublicLoading(false));
      if (exampleId) {
        const manifest = await manifestPromise;
        const example = manifest?.examples.find((item) => item.id === exampleId);
        if (!example) {
          setExampleError(`Example “${exampleId}” was not found. Browse the available reviews below.`);
          return;
        }
        setReport(await fetchExampleReport(example));
        setReportOrigin("example");
        setPhase("report");
        return;
      }
      if (publicSlug) {
        setReport(await api.publicReport(publicSlug));
        setReportOrigin("public");
        setPhase("report");
        return;
      }
      setSession(await api.session());
      if (analysisId) {
        setPhase("running");
        await pollAnalysis(analysisId);
      } else if (paper) {
        setQuery(paper);
        queryRef.current = paper;
        await resolveValue(paper, false);
      }
    };
    void bootstrap().catch((caught) => {
      setError(errorMessage(caught));
      setPhase("input");
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    if (viewerUrlRef.current) URL.revokeObjectURL(viewerUrlRef.current);
  }, []);

  const freshAnalysisAvailable = Boolean(
    session?.live_analysis_enabled
    && session.hosted_remaining !== 0
    && session.hosted_capacity_available,
  );

  const retainViewerPdf = (blob: Blob) => {
    if (viewerUrlRef.current) URL.revokeObjectURL(viewerUrlRef.current);
    const url = URL.createObjectURL(blob);
    viewerUrlRef.current = url;
    setViewerUrl(url);
  };


  const canAnalyze = useMemo(() => {
    if (!session?.live_analysis_enabled) return false;
    if (mode === "upload") return Boolean(file);
    if (!query.trim()) return false;
    if (!resolution || resolvedQuery !== query.trim()) return true;
    const identity = resolution.identity;
    return Boolean(
      resolution.abstract
      || resolution.candidates?.length
      || identity.doi
      || identity.arxiv_id
      || identity.pmid
      || identity.pmcid
      || identity.title,
    );
  }, [file, mode, query, resolution, resolvedQuery, session]);

  const reset = () => {
    if (viewerUrlRef.current) URL.revokeObjectURL(viewerUrlRef.current);
    viewerUrlRef.current = "";
    setPhase("input"); setResolution(null); setResolvedQuery(""); setCandidateId(""); setStatus(null); setReport(null); setReportOrigin("live"); setError(""); setLocalStage(""); setFile(null); setQuery(""); setCancelling(false); setVisibility("private"); setReuseMatch(null); setPreparedDocumentId(""); setViewerUrl("");
    queryRef.current = "";
    resolutionRequest.current = null;
    window.history.replaceState({}, "", window.location.pathname);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const changeMode = (nextMode: InputMode) => {
    setMode(nextMode);
    setResolution(null);
    setPhase("input");
    setReuseMatch(null);
    setPreparedDocumentId("");
  };

  const handleModeKeyDown = (event: JSX.TargetedKeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentMode: InputMode = event.currentTarget.id.endsWith("upload") ? "upload" : "identifier";
    const nextMode: InputMode = event.key === "Home"
      ? "identifier"
      : event.key === "End"
        ? "upload"
        : currentMode === "identifier"
          ? "upload"
          : "identifier";
    changeMode(nextMode);
    document.getElementById(`paper-mode-${nextMode}`)?.focus();
  };

  const resolveValue = (value: string, updateHistory = true): Promise<ResolvedPaper> => {
    const normalized = value.trim();
    if (!normalized) return Promise.reject(new Error("Enter a paper identifier or URL."));
    if (resolution && resolvedQuery === normalized) return Promise.resolve(resolution);
    if (resolutionRequest.current?.value === normalized) return resolutionRequest.current.promise;
    setError("");
    setPhase("resolving");
    const promise = api.resolve(normalized)
      .then((result) => {
        if (queryRef.current.trim() === normalized) {
          setResolution(result);
          setResolvedQuery(normalized);
          setCandidateId(result.candidates?.[0]?.id || "");
          setPhase("resolved");
          if (updateHistory) {
            const params = new URLSearchParams({ paper: normalized });
            window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
          }
        }
        return result;
      })
      .catch((caught) => {
        if (queryRef.current.trim() === normalized) {
          setError(errorMessage(caught));
          setPhase("input");
        }
        throw caught;
      })
      .finally(() => {
        if (resolutionRequest.current?.value === normalized) resolutionRequest.current = null;
      });
    resolutionRequest.current = { value: normalized, promise };
    return promise;
  };

  // The resolver request is deduplicated through resolutionRequest; input changes own this timer.
  useEffect(() => {
    if (mode !== "identifier" || !isResolvableInput(query) || resolvedQuery === query.trim()) return;
    const timer = window.setTimeout(() => { void resolveValue(query).catch(() => undefined); }, 650);
    return () => window.clearTimeout(timer);
  }, [mode, query, resolvedQuery]); // eslint-disable-line react-hooks/exhaustive-deps

  async function pollAnalysis(analysisId: string) {
    let current = await api.status(analysisId);
    setStatus(current);
    while (!(["completed", "failed", "cancelled"] as string[]).includes(current.state)) {
      await wait(1200);
      current = await api.status(current.id);
      setStatus(current);
    }
    if (current.state === "cancelled") {
      setCancelling(false);
      setError("Review cancelled.");
      setPhase(resolution ? "resolved" : "input");
      return;
    }
    if (current.state !== "completed") throw new Error(current.error || `Analysis ${current.state}.`);
    const completedReport = await api.report(current.id);
    setReport(completedReport);
    setReportOrigin("live");
    setPhase("report");
  }

  const prepareDocument = async (activeResolution: ResolvedPaper | null, activeCandidateId: string): Promise<DocumentReceipt> => {
    if (mode === "upload" && file) {
      setLocalStage("Parsing PDF locally with PDF.js");
      const document = await parsePdf(await file.arrayBuffer());
      retainViewerPdf(file);
      return api.createDocument(document);
    }
    if (!activeResolution) throw new Error("The paper could not be resolved.");
    const failedCandidateIds: string[] = [];
    const candidates = orderedCandidates(activeResolution, activeCandidateId);
    for (const candidate of candidates) {
      if (candidate.format === "pdf") {
        let document: PaperDocument;
        try {
          const previous = candidates.find((item) => item.id === failedCandidateIds.at(-1));
          setLocalStage(previous
            ? `${sourceLabel(previous)} unavailable; trying ${sourceLabel(candidate)}`
            : `Retrieving ${sourceLabel(candidate)}`);
          const bytes = await api.relayPdf(activeResolution.id, candidate.id);
          setLocalStage(`Parsing ${sourceLabel(candidate)} locally with PDF.js`);
          document = await parsePdf(bytes, activeResolution.identity);
          document.source_url = candidate.url ?? undefined;
          document.source_provider = candidate.provider;
          document.source_version = candidate.version ?? undefined;
          retainViewerPdf(new Blob([bytes], { type: "application/pdf" }));
        } catch (caught) {
          if (caught instanceof ApiError && caught.status !== 502) throw caught;
          failedCandidateIds.push(candidate.id);
          continue;
        }
        (document.extraction_warnings ??= []).push(
          ...fallbackWarnings(activeResolution, failedCandidateIds, candidate),
        );
        return api.createDocument(document);
      }
      if (candidate.format === "jats") {
        try {
          const previous = candidates.find((item) => item.id === failedCandidateIds.at(-1));
          setLocalStage(previous
            ? `${sourceLabel(previous)} unavailable; trying ${sourceLabel(candidate)}`
            : `Normalizing ${sourceLabel(candidate)} with stable paragraph anchors`);
          return await api.createJatsDocument(activeResolution.id, candidate.id, failedCandidateIds);
        } catch (caught) {
          if (!(caught instanceof ApiError) || caught.status !== 502) throw caught;
          failedCandidateIds.push(candidate.id);
        }
      }
    }
    setLocalStage(`Full text unavailable; preparing ${words(activeResolution.content_level)} content`);
    return api.createDocument(await metadataDocument(activeResolution, failedCandidateIds));
  };

  const startFreshAnalysis = async (documentId: string) => {
    const initial = await api.analyze(documentId, visibility);
    setReuseMatch(null);
    setStatus(initial);
    setPhase("running");
    window.history.replaceState({}, "", `${window.location.pathname}?${new URLSearchParams({ analysis: initial.id })}`);
    await pollAnalysis(initial.id);
    setSession(await api.session());
  };

  const openReusableAnalysis = async () => {
    if (!reuseMatch) return;
    setError("");
    if (reuseMatch.access === "public" && reuseMatch.slug) {
      const params = new URLSearchParams({ public: reuseMatch.slug });
      window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
      setReport(await api.publicReport(reuseMatch.slug));
      setReportOrigin("public");
      setPhase("report");
      return;
    }
    if (reuseMatch.analysis_id) {
      window.history.replaceState({}, "", `${window.location.pathname}?${new URLSearchParams({ analysis: reuseMatch.analysis_id })}`);
      setPhase("running");
      await pollAnalysis(reuseMatch.analysis_id);
    }
  };

  const analyze = async () => {
    setError("");
    try {
      let activeResolution = mode === "identifier" ? resolution : null;
      let activeCandidateId = candidateId;
      if (mode === "identifier" && (!activeResolution || resolvedQuery !== query.trim())) {
        setLocalStage("Finding the best available paper source");
        const pendingResolution = resolveValue(query);
        setPhase("preparing");
        activeResolution = await pendingResolution;
        activeCandidateId = activeResolution.candidates?.[0]?.id || "";
      }
      setPhase("preparing");
      const receipt = await prepareDocument(activeResolution, activeCandidateId);
      setPreparedDocumentId(receipt.id);
      const match = await api.reusableAnalysis(receipt.id);
      if (match) {
        setReuseMatch(match);
        setLocalStage("");
        setPhase(resolution ? "resolved" : "input");
        return;
      }
      await startFreshAnalysis(receipt.id);
    } catch (caught) {
      setError(errorMessage(caught));
      setPhase(resolution ? "resolved" : "input");
    }
  };

  const cancelAnalysis = async () => {
    if (!status || cancelling) return;
    setCancelling(true);
    try {
      setStatus(await api.cancel(status.id));
    } catch (caught) {
      setCancelling(false);
      setError(errorMessage(caught));
    }
  };

  return (
    <div class="app-shell">
      <Header onReset={reset} />
      {phase === "report" && report ? <Report report={report} onReset={reset} origin={reportOrigin} viewerUrl={viewerUrl || undefined} /> : phase === "preparing" || phase === "running" ? <Progress status={status} localStage={localStage} cancelling={cancelling} onCancel={() => void cancelAnalysis()} /> : (
        <main class="intake-page">
          <section class="hero">
            <div>
              <span class="eyebrow">Paper in. Evidence out.</span>
              <h1>A structured review that shows its work.</h1>
              <p>Submit one paper. Get content-aware methodology findings, quoted evidence, and explicit gaps—without pretending an abstract is a full-text review.</p>
            </div>
            <ScopeNote />
          </section>

          <section class="intake-card">
            <div class="mode-tabs" role="tablist" aria-label="Paper input type">
              <button
                id="paper-mode-identifier"
                class={mode === "identifier" ? "active" : ""}
                type="button"
                role="tab"
                aria-controls="paper-panel-identifier"
                aria-selected={mode === "identifier"}
                tabIndex={mode === "identifier" ? 0 : -1}
                onClick={() => changeMode("identifier")}
                onKeyDown={handleModeKeyDown}
              >
                Identifier or URL
              </button>
              <button
                id="paper-mode-upload"
                class={mode === "upload" ? "active" : ""}
                type="button"
                role="tab"
                aria-controls="paper-panel-upload"
                aria-selected={mode === "upload"}
                tabIndex={mode === "upload" ? 0 : -1}
                onClick={() => changeMode("upload")}
                onKeyDown={handleModeKeyDown}
              >
                Upload PDF
              </button>
            </div>
            {mode === "identifier" ? (
              <div
                id="paper-panel-identifier"
                class="resolver-form"
                role="tabpanel"
                aria-labelledby="paper-mode-identifier"
              >
                <label class="field input-large">
                  <span>DOI, arXiv ID, PMID, PMCID, or scholarly URL</span>
                  <input
                    aria-describedby="identifier-help"
                    value={query}
                    onInput={(event) => {
                      const value = event.currentTarget.value;
                      setQuery(value);
                      queryRef.current = value;
                      setReuseMatch(null);
                      setPreparedDocumentId("");
                      if (resolvedQuery !== value.trim()) {
                        setResolution(null);
                        setCandidateId("");
                        setPhase("input");
                      }
                    }}
                    onBlur={() => { if (query.trim()) void resolveValue(query).catch(() => undefined); }}
                    onKeyDown={(event) => { if (event.key === "Enter" && canAnalyze) void analyze(); }}
                    placeholder="10.1038/…  ·  arXiv:…  ·  pubmed.ncbi.nlm.nih.gov/…"
                  />
                  <small id="identifier-help" aria-live="polite">{phase === "resolving" ? "Finding metadata and candidate open full-text sources…" : resolution ? "Metadata resolved. Full-text candidates are verified when analysis starts; unavailable sources fall back automatically." : "Metadata is checked before analysis; full text is retrieved only after you start."}</small>
                </label>
              </div>
            ) : (
              <div id="paper-panel-upload" role="tabpanel" aria-labelledby="paper-mode-upload">
                <label class="drop-zone">
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(event) => {
                      setFile(event.currentTarget.files?.[0] || null);
                      setReuseMatch(null);
                      setPreparedDocumentId("");
                      if (viewerUrlRef.current) URL.revokeObjectURL(viewerUrlRef.current);
                      viewerUrlRef.current = "";
                      setViewerUrl("");
                    }}
                  />
                  <span class="drop-icon" aria-hidden="true">↓</span>
                  <strong>{file ? file.name : "Choose a PDF"}</strong>
                  <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · parsed locally` : "Up to 25 MB · parsed locally with PDF.js"}</small>
                </label>
              </div>
            )}
            <VisibilityControl value={visibility} onChange={setVisibility} />
            {resolution && <ResolutionCard resolution={resolution} selected={candidateId} onSelect={(id) => { setCandidateId(id); setReuseMatch(null); setPreparedDocumentId(""); }} />}
            {reuseMatch && (
              <ReusePrompt
                match={reuseMatch}
                freshAvailable={freshAnalysisAvailable}
                onReuse={() => void openReusableAnalysis().catch((caught) => setError(errorMessage(caught)))}
                onFresh={() => void startFreshAnalysis(preparedDocumentId).catch((caught) => {
                  setError(errorMessage(caught));
                  setPhase(resolution ? "resolved" : "input");
                })}
              />
            )}
            {error && <div class="error-box" role="alert">{error}</div>}
            <div class="action-row">
              <div>
                <span>{!session ? "Connecting to analysis service" : !session.live_analysis_enabled ? "Live analysis paused" : !session.hosted_capacity_available ? "Daily analysis capacity reached" : "Standard review"}</span>
                <small>{!session?.live_analysis_enabled || session.hosted_capacity_available ? "Full text when available · extracted text is sent to the configured review service · do not submit confidential material" : "Hosted analysis is temporarily unavailable · compatible existing reviews remain accessible"}</small>
              </div>
              <button class="analyze-button" type="button" disabled={!canAnalyze || Boolean(reuseMatch)} onClick={() => void analyze()}>{freshAnalysisAvailable ? "Analyze paper" : "Find existing review"} <span>→</span></button>
            </div>
          </section>
          <PublicFeed reports={publicReports} loading={publicLoading} error={publicError} />
          <ExampleGallery manifest={exampleManifest} loading={exampleLoading} error={exampleError} />
        </main>
      )}
      <footer><span>Sloppy Paper Checker</span><span>Private reports expire after 24 hours · Public reports expire after 30 days</span></footer>
    </div>
  );
}
