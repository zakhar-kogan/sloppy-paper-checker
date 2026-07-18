import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { ApiError, api } from "./api";
import type {
  AnalysisReport,
  AnalysisStatus,
  DocumentReceipt,
  PaperDocument,
  ResolvedPaper,
} from "./domain";
import { duration, errorMessage, fallbackWarnings, isResolvableInput, orderedCandidates, sourceLabel } from "./intake";
import { parsePdf } from "./pdf";
import { buildAssessmentGroups, coverageStateLabel, findingDisplayTitle, moduleStateLabel } from "./report";

type Phase = "input" | "resolving" | "resolved" | "preparing" | "running" | "report";
type InputMode = "identifier" | "upload";

const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const percent = (value: number) => `${Math.round(value * 100)}%`;
const words = (value: string) => value.replaceAll("_", " ");
const terminalStates = new Set(["completed", "failed", "skipped"]);

async function hashText(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
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
  return {
    schema_version: "1.0",
    identity,
    content_level: resolution.abstract ? "abstract" : "metadata",
    source_format: resolution.abstract ? "abstract" : "metadata",
    sha256: await hashText(metadata),
    parser_name: "resolver-metadata",
    parser_version: "1.0",
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
        <div><dt>Best available</dt><dd>{selectedCandidate ? `${selectedCandidate.format.toUpperCase()} · ${selectedCandidate.provider}` : words(resolution.content_level)}</dd></div>
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

function Report({ report, onReset }: { report: AnalysisReport; onReset: () => void }) {
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
      <section class="report-title">
        <span class="eyebrow">Automated evidence review</span>
        <h1>{title}</h1>
        <p>{report.identity.authors?.slice(0, 5).join(", ")}</p>
      </section>

      {report.banners.map((banner) => <div class="record-banner" key={banner}>{banner} {(report.context.record_sources ?? []).map((source) => <a href={source.url} target="_blank" rel="noreferrer" key={source.url}>Source ↗</a>)}</div>)}
      {(report.execution_warnings ?? []).map((warning) => <div class="record-banner" key={warning}>{warning}</div>)}

      <section class="takeaways">
        <div>
          <span class="eyebrow">Read this first</span>
          <h2>Scope & major takeaways</h2>
          <ul>{(report.summary ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div class="score-card">
          <span class="score-label">Full-review coverage</span>
          <strong>{percent(report.coverage.full_review)}</strong>
          <p class={report.coverage.provisional ? "provisional" : "complete-label"}>{coverageStateLabel(report.coverage.provisional)}</p>
          <dl>
            <div><dt>Grounded concerns</dt><dd>{(gradeCounts.critical_concern ?? 0) + (gradeCounts.major_concern ?? 0) + (gradeCounts.minor_concern ?? 0)}</dd></div>
            <div><dt>No concern</dt><dd>{gradeCounts.no_concern ?? 0}</dd></div>
            <div><dt>Items assessed</dt><dd>{report.assessed_item_count ?? report.findings.filter((item) => item.grade !== "not_assessed").length}</dd></div>
            <div><dt>Content</dt><dd>{words(report.content_level)}</dd></div>
            <div><dt>Available-content coverage</dt><dd>{percent(report.coverage.available)}</dd></div>
            <div><dt>Coverage-weighted heuristic</dt><dd>{hasFinalScore ? `${Math.round(report.review_score)}/100` : "—"}</dd></div>
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

      <button class="secondary-button new-review" type="button" onClick={onReset}>Review another paper</button>
    </main>
  );
}

export default function App() {
  const [phase, setPhase] = useState<Phase>("input");
  const [mode, setMode] = useState<InputMode>("identifier");
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [resolution, setResolution] = useState<ResolvedPaper | null>(null);
  const [resolvedQuery, setResolvedQuery] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [error, setError] = useState("");
  const [localStage, setLocalStage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const queryRef = useRef("");
  const resolutionRequest = useRef<{ value: string; promise: Promise<ResolvedPaper> } | null>(null);

  // Bootstrap only once; later navigation is driven by the durable query parameters we write.
  useEffect(() => {
    api.session()
      .then(async () => {
        const params = new URLSearchParams(window.location.search);
        const analysisId = params.get("analysis");
        const paper = params.get("paper");
        if (analysisId) {
          setPhase("running");
          await pollAnalysis(analysisId);
        } else if (paper) {
          setQuery(paper);
          queryRef.current = paper;
          await resolveValue(paper, false);
        }
      })
      .catch((caught) => {
        setError(errorMessage(caught));
        setPhase("input");
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const canAnalyze = useMemo(() => {
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
  }, [file, mode, query, resolution, resolvedQuery]);

  const reset = () => {
    setPhase("input"); setResolution(null); setResolvedQuery(""); setCandidateId(""); setStatus(null); setReport(null); setError(""); setLocalStage(""); setFile(null); setQuery(""); setCancelling(false);
    queryRef.current = "";
    resolutionRequest.current = null;
    window.history.replaceState({}, "", window.location.pathname);
  };

  const changeMode = (nextMode: InputMode) => {
    setMode(nextMode);
    setResolution(null);
    setPhase("input");
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
    setPhase("report");
  }

  const prepareDocument = async (activeResolution: ResolvedPaper | null, activeCandidateId: string): Promise<DocumentReceipt> => {
    if (mode === "upload" && file) {
      setLocalStage("Parsing PDF locally with PDF.js");
      return api.createDocument(await parsePdf(await file.arrayBuffer()));
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
      const initial = await api.analyze(receipt.id);
      setStatus(initial); setPhase("running");
      window.history.replaceState({}, "", `${window.location.pathname}?${new URLSearchParams({ analysis: initial.id })}`);
      await pollAnalysis(initial.id);
      await api.session();
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
      {phase === "report" && report ? <Report report={report} onReset={reset} /> : phase === "preparing" || phase === "running" ? <Progress status={status} localStage={localStage} cancelling={cancelling} onCancel={() => void cancelAnalysis()} /> : (
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
                  <small id="identifier-help" aria-live="polite">{phase === "resolving" ? "Finding metadata and open full-text sources…" : resolution ? "Source preflight complete. You can change the version below." : "Sources are checked automatically before analysis."}</small>
                </label>
              </div>
            ) : (
              <div id="paper-panel-upload" role="tabpanel" aria-labelledby="paper-mode-upload">
                <label class="drop-zone">
                  <input type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.currentTarget.files?.[0] || null)} />
                  <span class="drop-icon" aria-hidden="true">↓</span>
                  <strong>{file ? file.name : "Choose a PDF"}</strong>
                  <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · parsed locally` : "Up to 25 MB · parsed locally with PDF.js"}</small>
                </label>
              </div>
            )}
            {resolution && <ResolutionCard resolution={resolution} selected={candidateId} onSelect={setCandidateId} />}
            {error && <div class="error-box" role="alert">{error}</div>}
            <div class="action-row">
              <div>
                <span>Standard review</span>
                <small>Full text when available · extracted text is sent to the configured review service · do not submit confidential material</small>
              </div>
              <button class="analyze-button" type="button" disabled={!canAnalyze} onClick={() => void analyze()}>Analyze paper <span>→</span></button>
            </div>
          </section>
        </main>
      )}
      <footer><span>Sloppy Paper Checker</span><span>Reports expire after 24 hours · No cited full texts retrieved</span></footer>
    </div>
  );
}
