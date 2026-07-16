import { describe, expect, it } from "vitest";
import { inferReferences, inferSections, normalizePageItems } from "./pdfNormalization";

describe("PDF.js normalization helpers", () => {
  it("extracts reference entries and DOI identifiers without retrieving them", () => {
    const text = "Results\nThe result was stable.\n\nReferences\nSmith J. Example. doi:10.1234/EXAMPLE.1\n\nAnother sufficiently long reference entry without a DOI.";
    const references = inferReferences(text);
    expect(references).toHaveLength(2);
    expect(references[0].doi).toBe("10.1234/example.1");
  });

  it("does not treat ordinary body paragraphs as a reference list", () => {
    expect(inferReferences("Methods\nThis paper cites prior work in prose.")).toEqual([]);
  });

  it("retains page offsets, line endings, and PDF bounding boxes", () => {
    const normalized = normalizePageItems(
      [
        { str: "Methods", transform: [1, 0, 0, 18, 24, 700], width: 70, height: 18, hasEOL: true },
        { str: "Participants were enrolled.", transform: [1, 0, 0, 10, 24, 670], width: 160, height: 10, hasEOL: false },
      ],
      { width: 612, height: 792 },
      2,
      100,
    );
    expect(normalized.text).toBe("Methods\nParticipants were enrolled. ");
    expect(normalized.spans.map((span) => [span.start, span.end])).toEqual([[100, 107], [108, 135]]);
    expect(normalized.spans[0].bbox).toEqual({ x: 24, y: 74, width: 70, height: 18 });
  });

  it("routes large-font short spans into stable section ranges", () => {
    const page = normalizePageItems(
      [
        { str: "Methods", transform: [1, 0, 0, 18, 20, 700], width: 70, height: 18, hasEOL: true },
        { str: "Body sentence.", transform: [1, 0, 0, 10, 20, 670], width: 90, height: 10, hasEOL: true },
        { str: "Results", transform: [1, 0, 0, 18, 20, 640], width: 65, height: 18, hasEOL: true },
        { str: "Another body sentence.", transform: [1, 0, 0, 10, 20, 610], width: 120, height: 10, hasEOL: true },
      ],
      { width: 612, height: 792 },
      1,
      0,
    );
    expect(inferSections(page.spans, page.text.length).map((section) => section.title)).toEqual(["Methods", "Results"]);
  });
});
