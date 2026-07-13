import {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {getReport} from "./lib/api";
import type {AnalysisReport} from "./lib/types";
import {ReportView} from "./components/ReportView";
import "./style.css";

const reportId = new URLSearchParams(location.search).get("id");
const validReportId = reportId && /^[0-9a-f-]{36}$/i.test(reportId) ? reportId : null;

function App() {
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [error, setError] = useState(validReportId ? "" : "The report identifier is invalid.");
  useEffect(() => {
    if (validReportId) void getReport(validReportId).then(setReport, exception => setError(exception.message));
  }, []);
  return report ? <ReportView report={report}/> : <main className="loading-page"><div className="mark">SPC<span>β</span></div><p>{error || "Opening evidence dossier…"}</p></main>;
}
createRoot(document.getElementById("root")!).render(<App/>);
