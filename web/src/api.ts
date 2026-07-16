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
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message || response.statusText;
    throw new ApiError(response.status, message, typeof detail === "object" ? detail?.code : undefined);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const api = {
  session: () => request<SessionView>("/v1/session", { method: "POST" }),
  resolve: (value: string) =>
    request<ResolvedPaper>("/v1/resolve", { method: "POST", body: JSON.stringify({ value }) }),
  relayPdf: async (resolutionId: string, candidateId: string) => {
    const response = await fetch(`/v1/resolutions/${resolutionId}/artifacts/${candidateId}`, {
      credentials: "include",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail;
      throw new ApiError(
        response.status,
        typeof detail === "object" ? detail?.message || "PDF retrieval failed" : detail || "PDF retrieval failed",
        typeof detail === "object" ? detail?.code : undefined,
      );
    }
    return response.arrayBuffer();
  },
  createDocument: (document: PaperDocument) =>
    request<DocumentReceipt>("/v1/documents", { method: "POST", body: JSON.stringify(document) }),
  createJatsDocument: (resolutionId: string, candidateId: string, failedCandidateIds: string[] = []) =>
    request<DocumentReceipt>(`/v1/resolutions/${resolutionId}/documents/${candidateId}`, {
      method: "POST",
      body: JSON.stringify({ failed_candidate_ids: failedCandidateIds }),
    }),
  analyze: (documentId: string) =>
    request<AnalysisStatus>("/v1/analyses", {
      method: "POST",
      body: JSON.stringify({ source: { kind: "document", value: documentId } }),
    }),
  status: (id: string) => request<AnalysisStatus>(`/v1/analyses/${id}`),
  cancel: (id: string) => request<AnalysisStatus>(`/v1/analyses/${id}/cancel`, { method: "POST" }),
  report: (id: string) => request<AnalysisReport>(`/v1/analyses/${id}/report`),
};
