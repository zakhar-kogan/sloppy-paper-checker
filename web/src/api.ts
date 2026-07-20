import type {
  AnalysisReport,
  AnalysisStatus,
  DocumentReceipt,
  PaperDocument,
  ResolvedPaper,
  PublicReportList,
  PublicReportSummary,
  ReusableAnalysis,
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
    throw new ApiError(
      response.status,
      message,
      response.headers.get("X-SPC-Error-Code") ?? (typeof detail === "object" ? detail?.code : undefined),
    );
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
  session: (signal?: AbortSignal) => request<SessionView>("/v1/session", { method: "POST", signal }),
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
        response.headers.get("X-SPC-Error-Code") ?? (typeof detail === "object" ? detail?.code : undefined),
      );
    }
    return response.arrayBuffer();
  },
  createDocument: (document: PaperDocument) =>
    request<DocumentReceipt>("/v1/documents", { method: "POST", body: JSON.stringify(document) }),
  createPmcDocument: (resolutionId: string, candidateId: string, failedCandidateIds: string[] = []) =>
    request<DocumentReceipt>(`/v1/resolutions/${resolutionId}/documents/${candidateId}`, {
      method: "POST",
      body: JSON.stringify({ failed_candidate_ids: failedCandidateIds }),
    }),
  reusableAnalysis: (documentId: string) =>
    request<ReusableAnalysis | null>(`/v1/documents/${documentId}/reusable-analysis`),
  analyze: (documentId: string, visibility: "private" | "public") =>
    request<AnalysisStatus>("/v1/analyses", {
      method: "POST",
      body: JSON.stringify({ source: { kind: "document", value: documentId }, visibility }),
    }),
  status: (id: string) => request<AnalysisStatus>(`/v1/analyses/${id}`),
  cancel: (id: string) => request<AnalysisStatus>(`/v1/analyses/${id}/cancel`, { method: "POST" }),
  report: (id: string) => request<AnalysisReport>(`/v1/analyses/${id}/report`),
  publication: (id: string) =>
    request<PublicReportSummary | null>(`/v1/analyses/${id}/publication`),
  publish: (id: string) =>
    request<PublicReportSummary>(`/v1/analyses/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ confirm_public: true }),
    }),
  unpublish: (id: string) =>
    request<void>(`/v1/analyses/${id}/publish`, { method: "DELETE" }),
  publicReports: () => request<PublicReportList>("/v1/public/reports"),
  publicReport: (slug: string) => request<AnalysisReport>(`/v1/public/reports/${slug}`),
};
