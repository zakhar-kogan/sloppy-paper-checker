import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "./api";
import type {
  AnalysisReport,
  AnalysisStatus,
  DocumentReceipt,
  PaperDocument,
  ResolvedPaper,
  SessionView,
} from "./domain";
import { parsePdf } from "./pdf";

type Phase = "input" | "resolving" | "resolved" | "preparing" | "running" | "report";

const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const percent = (value: number) => `${Math.round(value * 100)}%`;
const words = (value: string) => value.replaceAll("_", " ");

async function hashText(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

async function metadataDocument(resolution: ResolvedPaper): Promise<PaperDocument> {
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
    extraction_warnings: resolution.limitations ?? [],
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

function Progress({ status, localStage }: { status: AnalysisStatus | null; localStage: string }) {
  const progress = status?.progress || (localStage ? 12 : 0);
  return (
    <main class="progress-page">
      <span class="eyebrow">Review in progress</span>
      <h1>{status?.stage || localStage}</h1>
      <div class="progress-track" aria-label={`${progress}% complete`}><span style={{ width: `${progress}%` }} /></div>
      <p>{progress}%</p>
      <ol class="stage-list">
        <li class={progress >= 8 ? "done" : ""}>Normalize paper</li>
        <li class={progress >= 22 ? "done" : ""}>Route relevant sections</li>
        <li class={progress >= 35 ? "done" : ""}>Run methodology modules</li>
        <li class={progress >= 84 ? "done" : ""}>Audit evidence</li>
        <li class={progress >= 100 ? "done" : ""}>Compile report</li>
      </ol>
      <p class="quiet">You can leave this tab open. The web client polls for stage updates; it does not stream partial findings.</p>
    </main>
  );
}

function Report({ report, onReset }: { report: AnalysisReport; onReset: () => void }) {
  const title = report.identity.title || report.identity.doi || "Paper review";
  const findings = report.findings.filter((finding) => finding.critic_disposition !== "discarded");
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
          <span class="score-label">Review score</span>
          <strong>{Math.round(report.review_score)}</strong><span class="score-denominator">/100</span>
          <p class={report.coverage.provisional ? "provisional" : "complete-label"}>{report.coverage.provisional ? "Provisional" : "Reviewed"}</p>
          <dl>
            <div><dt>Analysis confidence</dt><dd>{Math.round(report.confidence_score ?? 0)}%</dd></div>
            <div><dt>Items assessed</dt><dd>{report.assessed_item_count ?? report.findings.filter((item) => item.grade !== "not_assessed").length}</dd></div>
            <div><dt>Content</dt><dd>{words(report.content_level)}</dd></div>
            <div><dt>Available-content coverage</dt><dd>{percent(report.coverage.available)}</dd></div>
            <div><dt>Full-review coverage</dt><dd>{percent(report.coverage.full_review)}</dd></div>
          </dl>
        </div>
      </section>

      <section class="module-section">
        <div class="section-heading"><span class="index">01</span><div><span class="eyebrow">Methodology modules</span><h2>What was—and was not—assessed</h2></div></div>
        <div class="module-grid">
          {(report.module_statuses ?? []).map((module) => {
            const dimension = report.dimensions.find((item) => item.key === module.key);
            return (
              <article class="module-card" key={module.key}>
                <div class="module-top"><h3>{module.label}</h3><span>{dimension?.score ?? 0}</span></div>
                <p class={`module-state state-${module.state}`}>{words(module.state)}</p>
                <p>{module.assessed_items} of {module.expected_items} expected items assessed</p>
                {module.limitation && <small>{module.limitation}</small>}
              </article>
            );
          })}
        </div>
      </section>

      <section class="ledger-section">
        <div class="section-heading"><span class="index">02</span><div><span class="eyebrow">Technical evidence ledger</span><h2>Findings tied to the paper</h2></div></div>
        <div class="ledger">
          {findings.map((finding) => (
            <details class={`finding severity-${finding.severity}`} key={finding.id} open={finding.severity === "major" || finding.severity === "critical"}>
              <summary>
                <span class="finding-grade">{words(finding.grade)}</span>
                <strong>{finding.title}</strong>
                <span>{Math.round(finding.confidence * 100)}% confidence</span>
              </summary>
              <div class="finding-body">
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
        </div>
      </section>

      <section class="audit-section">
        <div class="section-heading"><span class="index">03</span><div><span class="eyebrow">Reproducibility</span><h2>Provenance & audit</h2></div></div>
        <dl class="audit-grid">
          <div><dt>Methodology</dt><dd>{report.methodology_version}<code>{report.methodology_hash.slice(0, 12)}</code></dd></div>
          <div><dt>Parser</dt><dd>{report.parser_name} {report.parser_version}</dd></div>
          <div><dt>Provider profile</dt><dd>{report.provider_profile}<br />{report.provider_protocol}</dd></div>
          <div><dt>Models</dt><dd>Worker: {report.worker_model || "deterministic fallback"}<br />Reviewer: {report.reviewer_model || "not run"}</dd></div>
          <div><dt>Paper content hash</dt><dd><code>{report.paper_sha256.slice(0, 18)}…</code></dd></div>
          <div><dt>Token usage</dt><dd>{Object.keys(report.token_usage ?? {}).length ? Object.entries(report.token_usage ?? {}).map(([key, value]) => `${key}: ${value}`).join(" · ") : "No model usage recorded"}</dd></div>
          <div><dt>Confidence components</dt><dd>Assessment: {percent(report.confidence_components?.assessment_coverage ?? 0)}<br />Evidence modules: {percent(report.confidence_components?.evidence_module_coverage ?? 0)}<br />Verified quotes: {percent(report.confidence_components?.quote_grounding_rate ?? 0)}</dd></div>
        </dl>
        <details class="limitations"><summary>Limitations and execution record</summary><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
      </section>

      <button class="secondary-button new-review" type="button" onClick={onReset}>Review another paper</button>
    </main>
  );
}

export default function App() {
  const [phase, setPhase] = useState<Phase>("input");
  const [mode, setMode] = useState<"identifier" | "upload">("identifier");
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [resolution, setResolution] = useState<ResolvedPaper | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [session, setSession] = useState<SessionView | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [error, setError] = useState("");
  const [localStage, setLocalStage] = useState("");

  useEffect(() => {
    api.session()
      .then(async (nextSession) => {
        setSession(nextSession);
        const params = new URLSearchParams(window.location.search);
        const analysisId = params.get("analysis");
        const paper = params.get("paper");
        if (analysisId) {
          setPhase("running");
          await pollAnalysis(analysisId);
        } else if (paper) {
          setQuery(paper);
          await resolveValue(paper, false);
        }
      })
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : String(caught));
        setPhase("input");
      });
  }, []);

  const canAnalyze = useMemo(() => {
    return mode === "upload" ? Boolean(file) : Boolean(resolution);
  }, [file, mode, resolution]);

  const reset = () => {
    setPhase("input"); setResolution(null); setCandidateId(""); setStatus(null); setReport(null); setError(""); setLocalStage(""); setFile(null); setQuery("");
    window.history.replaceState({}, "", window.location.pathname);
  };

  const resolveValue = async (value: string, updateHistory = true) => {
    if (!value.trim()) return;
    setError(""); setPhase("resolving");
    try {
      const result = await api.resolve(value.trim());
      setResolution(result);
      setCandidateId(result.candidates?.[0]?.id || "");
      setPhase("resolved");
      if (updateHistory) {
        const params = new URLSearchParams({ paper: value.trim() });
        window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPhase("input");
    }
  };

  const resolve = async () => {
    await resolveValue(query);
  };

  async function pollAnalysis(analysisId: string) {
    let current = await api.status(analysisId);
    setStatus(current);
    while (!(["completed", "failed", "cancelled"] as string[]).includes(current.state)) {
      await wait(1200);
      current = await api.status(current.id);
      setStatus(current);
    }
    if (current.state !== "completed") throw new Error(current.error || `Analysis ${current.state}.`);
    const completedReport = await api.report(current.id);
    setReport(completedReport);
    setPhase("report");
  }

  const prepareDocument = async (): Promise<DocumentReceipt> => {
    if (mode === "upload" && file) {
      setLocalStage("Parsing PDF locally with PDF.js");
      return api.createDocument(await parsePdf(await file.arrayBuffer()));
    }
    if (!resolution) throw new Error("Resolve a paper first.");
    const candidate = (resolution.candidates ?? []).find((item) => item.id === candidateId);
    if (candidate?.format === "pdf") {
      setLocalStage("Retrieving the resolved PDF through the bounded relay");
      const bytes = await api.relayPdf(resolution.id, candidate.id);
      setLocalStage("Parsing PDF locally with PDF.js");
      return api.createDocument(await parsePdf(bytes, resolution.identity));
    }
    if (candidate?.format === "jats") {
      setLocalStage("Normalizing PMC JATS with stable paragraph anchors");
      return api.createJatsDocument(resolution.id, candidate.id);
    }
    setLocalStage(`Preparing ${words(resolution.content_level)} content`);
    return api.createDocument(await metadataDocument(resolution));
  };

  const analyze = async () => {
    setError(""); setPhase("preparing");
    try {
      const receipt = await prepareDocument();
      const initial = await api.analyze(receipt.id);
      setStatus(initial); setPhase("running");
      window.history.replaceState({}, "", `${window.location.pathname}?${new URLSearchParams({ analysis: initial.id })}`);
      await pollAnalysis(initial.id);
      setSession(await api.session());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPhase(resolution ? "resolved" : "input");
    }
  };

  return (
    <div class="app-shell">
      <Header onReset={reset} />
      {phase === "report" && report ? <Report report={report} onReset={reset} /> : phase === "preparing" || phase === "running" ? <Progress status={status} localStage={localStage} /> : (
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
              <button class={mode === "identifier" ? "active" : ""} type="button" onClick={() => { setMode("identifier"); setResolution(null); setPhase("input"); }}>Identifier or URL</button>
              <button class={mode === "upload" ? "active" : ""} type="button" onClick={() => { setMode("upload"); setResolution(null); setPhase("input"); }}>Upload PDF</button>
            </div>
            {mode === "identifier" ? (
              <div class="resolver-form">
                <label class="field input-large"><span>DOI, arXiv ID, PMID, PMCID, or scholarly URL</span><input value={query} onInput={(event) => setQuery(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Enter") void resolve(); }} placeholder="10.1038/…  ·  arXiv:…  ·  pubmed.ncbi.nlm.nih.gov/…" /></label>
                <button class="resolve-button" type="button" disabled={!query.trim() || phase === "resolving"} onClick={() => void resolve()}>{phase === "resolving" ? "Resolving…" : "Resolve paper"}</button>
              </div>
            ) : (
              <label class="drop-zone">
                <input type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.currentTarget.files?.[0] || null)} />
                <span class="drop-icon">↓</span><strong>{file ? file.name : "Choose a PDF"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · parsed locally` : "Up to 25 MB · PDF.js parsing stays in this tab"}</small>
              </label>
            )}
            {resolution && <ResolutionCard resolution={resolution} selected={candidateId} onSelect={setCandidateId} />}
            {error && <div class="error-box" role="alert">{error}</div>}
            <div class="action-row">
              <div><span>Standard review</span><small>{session?.hosted_remaining ?? "—"} hosted runs remaining</small></div>
              <button class="analyze-button" type="button" disabled={!canAnalyze} onClick={() => void analyze()}>Analyze paper <span>→</span></button>
            </div>
          </section>
        </main>
      )}
      <footer><span>Sloppy Paper Checker</span><span>Reports expire after 24 hours · No cited full texts retrieved</span></footer>
    </div>
  );
}
