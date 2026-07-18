import type { ContentCandidate, ResolvedPaper } from "./domain";

export function errorMessage(caught: unknown): string {
  if (caught instanceof Error && caught.message && caught.message !== "[object Object]") return caught.message;
  if (caught && typeof caught === "object") {
    const value = caught as { message?: unknown; detail?: unknown };
    if (typeof value.message === "string" && value.message !== "[object Object]") return value.message;
    if (typeof value.detail === "string" && value.detail !== "[object Object]") return value.detail;
    if (value.detail && typeof value.detail === "object") {
      const detail = value.detail as { message?: unknown };
      if (typeof detail.message === "string") return detail.message;
    }
  }
  return "The paper source could not be prepared. Try another source version.";
}

export function isResolvableInput(value: string): boolean {
  const input = value.trim();
  return /^(https?:\/\/\S+|10\.\d{4,9}\/\S+|pmc\d+|pmid:\s*\d+|arxiv:\s*\S+|\d{4}\.\d{4,5}(v\d+)?|\d{5,9})$/i.test(input);
}

export function duration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder.toString().padStart(2, "0")}s` : `${remainder}s`;
}

export function orderedCandidates(resolution: ResolvedPaper, selectedId: string): ContentCandidate[] {
  const candidates = [...(resolution.candidates ?? [])].sort((left, right) => left.rank - right.rank);
  const selected = candidates.find((candidate) => candidate.id === selectedId);
  return selected ? [selected, ...candidates.filter((candidate) => candidate.id !== selected.id)] : candidates;
}

export function sourceLabel(candidate: ContentCandidate): string {
  return [candidate.format.toUpperCase(), candidate.provider, candidate.version]
    .filter((part): part is string => Boolean(part))
    .map((part) => part.replace(/\s+/g, " ").trim().slice(0, 80))
    .join(" · ");
}

export function fallbackWarnings(
  resolution: ResolvedPaper,
  failedCandidateIds: string[],
  usedCandidate?: ContentCandidate,
): string[] {
  const byId = new Map((resolution.candidates ?? []).map((candidate) => [candidate.id, candidate]));
  const usedLabel = usedCandidate
    ? sourceLabel(usedCandidate)
    : resolution.abstract
      ? "abstract only"
      : "metadata only";
  const failedLabels = new Set<string>();
  return [...new Set(failedCandidateIds)].flatMap((candidateId) => {
    const failed = byId.get(candidateId);
    if (!failed || failed.id === usedCandidate?.id) return [];
    const failedLabel = sourceLabel(failed);
    if (failedLabel === usedLabel || failedLabels.has(failedLabel)) return [];
    failedLabels.add(failedLabel);
    return [`${failedLabel} could not be used; analysis used ${usedLabel} instead.`];
  });
}
