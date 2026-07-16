import type { DocumentSection, DocumentSpan, ReferenceEntry } from "./pdfTypes";

export interface PdfTextFixture {
  str: string;
  transform: number[];
  width: number;
  height: number;
  hasEOL: boolean;
}

export function normalizePageItems(
  items: PdfTextFixture[],
  viewport: { width: number; height: number },
  pageNumber: number,
  initialOffset: number,
): { text: string; spans: DocumentSpan[] } {
  let text = "";
  const spans: DocumentSpan[] = [];
  for (const item of items) {
    if (!item.str.trim()) continue;
    const start = initialOffset + text.length;
    text += item.str;
    const end = initialOffset + text.length;
    spans.push({
      id: `pdf-${pageNumber}-${spans.length}`,
      text: item.str,
      start,
      end,
      page: pageNumber,
      bbox: {
        x: item.transform[4],
        y: Math.max(0, viewport.height - item.transform[5] - Math.abs(item.height || item.transform[3])),
        width: Math.max(0, item.width || 0),
        height: Math.max(0, Math.abs(item.height || item.transform[3])),
      },
    });
    text += item.hasEOL ? "\n" : " ";
  }
  return { text, spans };
}

export function inferSections(spans: DocumentSpan[], textLength: number): DocumentSection[] {
  const heights = spans.map((span) => span.bbox?.height || 0).filter(Boolean).sort((a, b) => a - b);
  const median = heights[Math.floor(Math.max(0, heights.length - 1) * 0.4)] || 10;
  const headings = spans.filter((span) => {
    const value = span.text.trim();
    return value.length >= 3 && value.length <= 100 && !/[.!?]$/.test(value) && (span.bbox?.height || 0) >= median * 1.2;
  });
  return headings.slice(0, 200).map((heading, index) => ({
    id: `pdf-section-${index}`,
    title: heading.text.trim(),
    start: heading.start,
    end: headings[index + 1]?.start ?? textLength,
    page_start: heading.page,
    page_end: headings[index + 1]?.page ?? heading.page,
  }));
}

export function inferReferences(text: string): ReferenceEntry[] {
  const match = /(?:^|\n)\s*(?:references|bibliography)\s*\n/i.exec(text);
  if (!match) return [];
  return text
    .slice(match.index + match[0].length)
    .split(/\n{2,}/)
    .map((raw) => raw.replace(/\s+/g, " ").trim())
    .filter((raw) => raw.length > 20)
    .slice(0, 5000)
    .map((raw, index) => ({
      id: `pdf-ref-${index}`,
      raw,
      doi: raw.match(/10\.\d{4,9}\/[._;()/:A-Z0-9-]+/i)?.[0].replace(/[.,;)]$/, "").toLowerCase(),
    }));
}
