import type { AnalysisReport, Finding } from "../lib/types";
import { ScoreDial } from "./ScoreDial";

function FindingCard({finding}: {finding: Finding}) {
  return <details className={`finding ${finding.severity}`}>
    <summary><span className="signal-dot"/><div><small>{finding.category} · {finding.grade.replaceAll("_", " ")}</small><h3>{finding.title}</h3></div><b>{Math.round(finding.confidence * 100)}%</b></summary>
    <div className="finding-body">
      <p>{finding.explanation}</p>
      {finding.paper_spans.map((span, index) => <blockquote key={index}>{span.page && <em>p. {span.page}</em>}{span.quote}</blockquote>)}
      {finding.external_sources.map(source => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a>)}
      {finding.limitations.length > 0 && <p className="limitation">Limits: {finding.limitations.join(" ")}</p>}
    </div>
  </details>;
}

export function ReportView({report, compact = false}: {report: AnalysisReport; compact?: boolean}) {
  return <main className={compact ? "report compact" : "report"}>
    <header className="report-head">
      <div><p className="eyebrow">analysis docket · {report.profile.replaceAll("_", " ")}</p><h1>{report.identity.title || report.identity.doi || "Untitled paper"}</h1><p className="byline">{report.identity.authors.join(", ") || "Author metadata unavailable"}</p></div>
      <ScoreDial score={report.composite_score} provisional={report.coverage.provisional}/>
    </header>
    {report.banners.map(banner => <div className="banner serious" key={banner}>{banner}</div>)}
    {report.coverage.provisional && <div className="banner">Provisional result · {Math.round(report.coverage.overall * 100)}% evidence coverage</div>}
    <section className="dimensions" aria-label="Score dimensions">
      {report.dimensions.map(item => <div className="dimension" key={item.key}><span>{item.label}<small>{item.weight}% weight</small></span><div className="meter"><i style={{width: `${item.assessed_items ? item.score : 0}%`}}/></div><b>{item.assessed_items ? Math.round(item.score) : "—"}</b></div>)}
    </section>
    <section className="findings-section"><div className="section-title"><p className="eyebrow">evidence ledger</p><h2>{report.findings.length} findings</h2></div>{report.findings.map(item => <FindingCard key={item.id} finding={item}/>)}</section>
    <details className="audit"><summary>Audit trail & limitations</summary><ul>{report.limitations.map(item => <li key={item}>{item}</li>)}</ul><ol>{report.audit_trail.map((event, index) => <li key={index}>{event.stage || "Event"} {event.progress != null && `· ${event.progress}%`}</li>)}</ol></details>
    <footer>Score v{report.scoring_version} · Navigation aid, not a validated risk-of-bias instrument.</footer>
  </main>;
}

