import * as pdfjsLib from "pdfjs-dist";
import type { TextItem } from "pdfjs-dist/types/src/display/api";
import type { DocumentPage, DocumentSpan, PaperDocument, PaperIdentity } from "./pdfTypes";
import { inferReferences, inferSections, normalizePageItems } from "./pdfNormalization";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const PDF_JS_VERSION = pdfjsLib.version;

function isTextItem(item: unknown): item is TextItem {
  return typeof item === "object" && item !== null && "str" in item && "transform" in item;
}

async function sha256(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function parsePdf(
  bytes: ArrayBuffer,
  identity: PaperIdentity = { authors: [], versions: [], fingerprint: "" },
): Promise<PaperDocument> {
  if (bytes.byteLength > 25 * 1024 * 1024) throw new Error("PDF exceeds the 25 MB demo limit.");
  let pdf: Awaited<ReturnType<typeof pdfjsLib.getDocument>["promise"]>;
  try {
    pdf = await pdfjsLib.getDocument({ data: bytes.slice(0) }).promise;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/password/i.test(message)) throw new Error("Encrypted PDFs are not supported.");
    throw new Error(`PDF.js could not open this file: ${message}`);
  }
  if (pdf.numPages > 300) throw new Error("PDF has more than the 300-page demo limit.");

  let text = "";
  const pages: DocumentPage[] = [];
  const spans: DocumentSpan[] = [];
  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1 });
    const content = await page.getTextContent();
    const pageStart = text.length;
    const normalized = normalizePageItems(
      content.items.filter(isTextItem),
      viewport,
      pageNumber,
      pageStart,
    );
    text += normalized.text;
    spans.push(...normalized.spans.map((span, index) => ({ ...span, id: `pdf-${pageNumber}-${spans.length + index}` })));
    pages.push({
      number: pageNumber,
      text: text.slice(pageStart),
      start: pageStart,
      end: text.length,
      width: viewport.width,
      height: viewport.height,
    });
    text += "\n";
  }
  if (text.trim().length < 80) {
    throw new Error("PDF.js found almost no text. This may be a scanned or image-only PDF.");
  }
  return {
    schema_version: "1.0",
    identity,
    content_level: "full_text",
    source_format: "pdf",
    sha256: await sha256(bytes),
    parser_name: "pdf.js",
    parser_version: PDF_JS_VERSION,
    text,
    pages,
    spans,
    sections: inferSections(spans, text.length),
    references: inferReferences(text),
    extraction_warnings: [],
  };
}
