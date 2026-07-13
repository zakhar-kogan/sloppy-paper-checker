import type { AnalysisReport, AnalysisStatus } from "./types";
import { getLocalSettings } from "./settings";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { apiBase, apiToken } = await getLocalSettings();
  if (!apiToken) throw new Error("Open settings and enter the backend access token for this browser session.");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${apiToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBase}${path}`, {...init, headers, credentials: "omit", redirect: "error"});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Backend returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function uploadPdf(blob: Blob, name = "paper.pdf"): Promise<string> {
  const form = new FormData();
  form.append("file", blob, name);
  const result = await request<{id: string}>("/v1/uploads", {method: "POST", body: form});
  return result.id;
}

export function createAnalysis(kind: "doi" | "url" | "upload", value: string, depth = "standard") {
  return request<AnalysisStatus>("/v1/analyses", {
    method: "POST",
    body: JSON.stringify({source: {kind, value}, depth, enabled_checks: [], sequential: false})
  });
}

export const getStatus = (id: string) => request<AnalysisStatus>(`/v1/analyses/${encodeURIComponent(id)}`);
export const getReport = (id: string) => request<AnalysisReport>(`/v1/analyses/${encodeURIComponent(id)}/report`);
export const cancelAnalysis = (id: string) => request<AnalysisStatus>(`/v1/analyses/${encodeURIComponent(id)}/cancel`, {method: "POST"});

export async function waitForReport(id: string, onStatus: (status: AnalysisStatus) => void): Promise<AnalysisReport> {
  for (;;) {
    const status = await getStatus(id);
    onStatus(status);
    if (status.state === "completed") return getReport(id);
    if (["failed", "cancelled"].includes(status.state)) throw new Error(status.error || `Analysis ${status.state}`);
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}

