import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import * as pdfjs from "../web/node_modules/pdfjs-dist/legacy/build/pdf.mjs";

const [pdfPath, identityPath, outputPath] = process.argv.slice(2);
if (!pdfPath || !identityPath || !outputPath) {
  throw new Error("Usage: node parse_showcase_pdf.mjs PDF IDENTITY_JSON OUTPUT_JSON");
}

const bytes = await readFile(pdfPath);
if (bytes.byteLength > 25 * 1024 * 1024) throw new Error("PDF exceeds the 25 MB limit.");
const identity = JSON.parse(await readFile(identityPath, "utf8"));
const pdf = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise;
if (pdf.numPages > 300) throw new Error("PDF has more than 300 pages.");

let text = "";
const pages = [];
const spans = [];
for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale: 1 });
  const content = await page.getTextContent();
  const pageStart = text.length;
  const pageText = content.items
    .filter((item) => typeof item === "object" && item !== null && "str" in item)
    .map((item) => item.str.trim())
    .filter(Boolean)
    .join(" ");
  text += `${pageText}\n`;
  pages.push({
    number: pageNumber,
    text: pageText,
    start: pageStart,
    end: text.length,
    width: viewport.width,
    height: viewport.height,
  });
  for (let offset = 0, part = 1; offset < pageText.length; part += 1) {
    let end = Math.min(offset + 8000, pageText.length);
    if (end < pageText.length) {
      const boundary = pageText.lastIndexOf(" ", end);
      if (boundary > offset) end = boundary;
    }
    spans.push({
      id: `pdf-page-${pageNumber}-${part}`,
      text: pageText.slice(offset, end),
      start: pageStart + offset,
      end: pageStart + end,
      page: pageNumber,
    });
    offset = end < pageText.length && pageText[end] === " " ? end + 1 : end;
  }
}
if (text.trim().length < 80) throw new Error("PDF.js found almost no text.");

const document = {
  schema_version: "1.0",
  identity,
  content_level: "full_text",
  source_format: "pdf",
  sha256: createHash("sha256").update(bytes).digest("hex"),
  parser_name: "pdf.js-node",
  parser_version: pdfjs.version,
  text,
  pages,
  spans,
  sections: [],
  references: [],
  extraction_warnings: ["Generated locally with the release-only Node PDF.js normalizer."],
};
await writeFile(outputPath, `${JSON.stringify(document)}\n`, { mode: 0o600 });
