import {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {cancelAnalysis, createAnalysis, uploadPdf, waitForReport} from "./lib/api";
import type {AnalysisReport, AnalysisStatus, PaperCandidate} from "./lib/types";
import {ReportView} from "./components/ReportView";
import "./style.css";

const CHECKS = ["Design", "Statistics", "Claims", "Transparency", "Reporting", "Record", "Authors"];

function App() {
  const [candidate, setCandidate] = useState<PaperCandidate | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [error, setError] = useState("");
  const [depth, setDepth] = useState("standard");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { void chrome.runtime.sendMessage({type: "GET_ACTIVE_PAPER"}).then(result => {
    if (result?.error) setError(result.error); else setCandidate(result);
  }); }, []);

  async function follow(kind: "doi" | "url" | "upload", value: string) {
    setError(""); setReport(null);
    try {
      const created = await createAnalysis(kind, value, depth);
      setStatus(created);
      const finished = await waitForReport(created.id, setStatus);
      setReport(finished);
      await chrome.runtime.sendMessage({type: "SET_BADGE", score: finished.composite_score});
    } catch (exception) { setError(exception instanceof Error ? exception.message : String(exception)); }
  }

  async function analyzeCurrent() {
    if (!candidate) return;
    if (candidate.captureLimitation) return fileRef.current?.click();
    if (!candidate.isPdf) return follow(candidate.kind, candidate.value);
    try {
      const captured = await chrome.runtime.sendMessage({type: "CAPTURE_PDF", url: candidate.value});
      if (captured?.error) throw new Error(captured.error);
      await follow("upload", captured.uploadId);
    } catch (exception) {
      setError(`${exception instanceof Error ? exception.message : String(exception)} Upload the file to continue.`);
    }
  }

  async function selectFile(file?: File) {
    if (!file) return;
    try { const id = await uploadPdf(file, file.name); await follow("upload", id); }
    catch (exception) { setError(exception instanceof Error ? exception.message : String(exception)); }
  }

  if (report) return <><ReportView report={report} compact/><div className="sticky-actions"><button onClick={() => chrome.tabs.create({url: chrome.runtime.getURL(`report.html?id=${encodeURIComponent(report.id)}`)})}>Open full dossier</button><button className="secondary" onClick={() => {setReport(null); setStatus(null);}}>Check another</button></div></>;

  return <main className="panel-shell">
    <header className="masthead"><div className="mark">SPC<span>β</span></div><button className="icon-button" aria-label="Open settings" onClick={() => chrome.runtime.openOptionsPage()}>⚙</button></header>
    <section className="intro"><p className="eyebrow">forensic reading assistant</p><h1>Interrogate the paper,<br/><em>not the reader.</em></h1><p>Traceable checks across methods, statistics, claims, transparency, and scholarly context.</p></section>
    {error && <div className="banner serious">{error}</div>}
    {status ? <section className="progress-card"><div className="progress-label"><span>{status.stage}</span><b>{status.progress}%</b></div><div className="progress"><i style={{width: `${status.progress}%`}}/></div><p>Specialists run independently, then an evidence critic removes unsupported findings.</p>{!['completed','failed','cancelled'].includes(status.state) && <button className="text-button" onClick={() => void cancelAnalysis(status.id)}>Cancel analysis</button>}</section> : <>
      <section className="source-card"><p className="eyebrow">active paper</p><h2>{candidate?.title || (candidate ? "Paper detected" : "Reading this tab…")}</h2>{candidate && <code>{candidate.kind === "doi" ? candidate.value : new URL(candidate.value).hostname}</code>}{candidate?.captureLimitation && <p className="limitation">{candidate.captureLimitation}</p>}</section>
      <div className="depth-picker" role="group" aria-label="Analysis depth">{["quick", "standard", "deep"].map(item => <button className={depth === item ? "active" : ""} onClick={() => setDepth(item)} key={item}>{item}</button>)}</div>
      <div className="check-grid">{CHECKS.map(item => <span key={item}>✓ {item}</span>)}</div>
      <button className="primary" disabled={!candidate} onClick={() => void analyzeCurrent()}>Analyze current paper <span>→</span></button>
      <button className="upload-button" onClick={() => fileRef.current?.click()}>or upload a PDF</button>
      <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden onChange={event => void selectFile(event.target.files?.[0])}/>
    </>}
    <p className="privacy-note">PDF bytes and full extracted text are deleted after analysis. Provider credentials remain on your backend.</p>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App/>);
