import { describe, expect, it } from "vitest";
import type { ContentCandidate, ResolvedPaper } from "./domain";
import { duration, errorMessage, fallbackWarnings, isResolvableInput, orderedCandidates } from "./intake";

const candidate = (id: string, rank: number, format: "pdf" | "jats", provider: string): ContentCandidate => ({
  id,
  rank,
  format,
  provider,
  version: "publishedVersion",
  content_level: "full_text",
  url: `https://example.org/${id}`,
  license: null,
});

const resolution = {
  abstract: "An abstract",
  candidates: [
    candidate("published-pdf", 10, "pdf", "Unpaywall"),
    candidate("accepted-pdf", 20, "pdf", "Unpaywall"),
    candidate("pmc-jats", 40, "jats", "PMC"),
  ],
} as ResolvedPaper;

describe("single-action paper intake", () => {
  it("never renders structured failures as object coercion noise", () => {
    expect(errorMessage({ detail: { message: "The selected PDF source is unavailable." } })).toBe(
      "The selected PDF source is unavailable.",
    );
    expect(errorMessage(new Error("[object Object]"))).toBe(
      "The paper source could not be prepared. Try another source version.",
    );
  });

  it("starts automatic preflight only for complete identifiers and URLs", () => {
    expect(isResolvableInput("10.1016/S0140-6736(17)32802-7")).toBe(true);
    expect(isResolvableInput("https://pubmed.ncbi.nlm.nih.gov/41366844/")).toBe(true);
    expect(isResolvableInput("PMC12910469")).toBe(true);
    expect(isResolvableInput("10.1016/")).toBe(false);
  });

  it("formats live stage duration without false precision", () => {
    expect(duration(7)).toBe("7s");
    expect(duration(72)).toBe("1m 12s");
  });

  it("tries the selected source first and every remaining ranked source once", () => {
    expect(orderedCandidates(resolution, "accepted-pdf").map((item) => item.id)).toEqual([
      "accepted-pdf",
      "published-pdf",
      "pmc-jats",
    ]);
  });

  it("creates bounded fallback provenance for a successful full-text source", () => {
    expect(fallbackWarnings(resolution, ["published-pdf", "accepted-pdf", "published-pdf"], resolution.candidates![2]))
      .toEqual([
        "PDF · Unpaywall · publishedVersion could not be used; analysis used JATS · PMC · publishedVersion instead.",
      ]);
  });

  it("labels metadata fallback explicitly after every full-text source fails", () => {
    expect(fallbackWarnings({ ...resolution, abstract: null }, ["published-pdf"])[0]).toContain(
      "analysis used metadata only instead",
    );
  });

  it("collapses fallback failures that have the same public source label", () => {
    const duplicate = {
      ...resolution.candidates![0],
      id: "published-pdf-copy",
      url: "https://mirror.example/published.pdf",
    };
    const withDuplicate = { ...resolution, candidates: [...resolution.candidates!, duplicate] };
    expect(fallbackWarnings(withDuplicate, ["published-pdf", duplicate.id], resolution.candidates![2]))
      .toHaveLength(1);
  });
});
