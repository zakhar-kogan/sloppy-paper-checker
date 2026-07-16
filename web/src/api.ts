import type {
  AnalysisReport,
  AnalysisStatus,
  DocumentReceipt,
  PaperDocument,
  ResolvedPaper,
  SessionView,
} from "./domain";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const api = {
  session: () => request<SessionView>("/v1/session", { method: "POST" }),
  resolve: (value: string) =>
    request<ResolvedPaper>("/v1/resolve", { method: "POST", body: JSON.stringify({ value }) }),
  relayPdf: async (resolutionId: string, candidateId: string) => {
    const response = await fetch(`/v1/resolutions/${resolutionId}/artifacts/${candidateId}`, {
      credentials: "include",
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "PDF retrieval failed");
    return response.arrayBuffer();
  },
  createDocument: (document: PaperDocument) =>
    request<DocumentReceipt>("/v1/documents", { method: "POST", body: JSON.stringify(document) }),
  createJatsDocument: (resolutionId: string, candidateId: string) =>
    request<DocumentReceipt>(`/v1/resolutions/${resolutionId}/documents/${candidateId}`, { method: "POST" }),
  analyze: (documentId: string) =>
    request<AnalysisStatus>("/v1/analyses", {
      method: "POST",
      body: JSON.stringify({ source: { kind: "document", value: documentId } }),
    }),
  status: (id: string) => request<AnalysisStatus>(`/v1/analyses/${id}`),
  report: (id: string) => request<AnalysisReport>(`/v1/analyses/${id}/report`),
};
