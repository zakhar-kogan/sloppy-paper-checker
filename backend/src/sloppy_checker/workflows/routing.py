from __future__ import annotations

import re
from dataclasses import dataclass

from sloppy_checker.core.methodology import ModuleDefinition
from sloppy_checker.core.schemas import PaperDocument


@dataclass(frozen=True)
class RoutedChunk:
    id: str
    text: str
    start: int
    end: int
    page: int | None
    section: str | None


DISCLOSURE_HEADINGS = re.compile(
    r"(?im)^(?:role of (?:the )?funding source|funding(?: statement)?|funding/support|"
    r"declaration of interests?|conflicts? of interest(?: disclosures?)?|"
    r"competing interests?|author information|author affiliations?|affiliations?|"
    r"acknowledg(?:e)?ments?)\s*:?[ \t]*"
)
DISCLOSURE_SECTION_TERMS = (
    "fund",
    "sponsor",
    "grant",
    "conflict",
    "competing",
    "interest",
    "affiliation",
    "author information",
    "acknowledg",
)


def _page_at(document: PaperDocument | None, offset: int) -> int | None:
    if not document:
        return None
    return next(
        (page.number for page in document.pages if page.start <= offset < page.end),
        None,
    )


def _section_at(document: PaperDocument | None, offset: int) -> str | None:
    if not document:
        return None
    candidates = [
        section
        for section in document.sections
        if section.start <= offset < section.end
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda section: section.end - section.start).title


def chunk_document(
    document_or_text: PaperDocument | str,
    target_chars: int = 4200,
    overlap: int = 350,
) -> list[RoutedChunk]:
    document = document_or_text if isinstance(document_or_text, PaperDocument) else None
    text = document.text if document else document_or_text
    page_markers = list(re.finditer(r"\[Page (\d+)\]", text)) if not document else []
    chunks: list[RoutedChunk] = []
    start = 0
    index = 0
    while start < len(text):
        ideal_end = min(len(text), start + target_chars)
        end = ideal_end
        if ideal_end < len(text):
            minimum_boundary = start + target_chars // 2
            boundary = max(
                text.rfind("\n\n", minimum_boundary, ideal_end),
                text.rfind(". ", minimum_boundary, ideal_end),
            )
            if boundary >= minimum_boundary:
                end = boundary + 2
        if end <= start:
            end = ideal_end
        page = _page_at(document, start)
        if page is None:
            for marker in page_markers:
                if marker.start() <= start:
                    page = int(marker.group(1))
                else:
                    break
        sample = text[start : min(end, start + 300)]
        heading = re.search(
            r"(?:^|\n)\[?([A-Z][A-Za-z0-9 &/:-]{2,80})\]?\s*(?:\n|$)", sample
        )
        section = _section_at(document, start) or (heading.group(1) if heading else None)
        chunks.append(
            RoutedChunk(f"chunk-{index}", text[start:end], start, end, page, section)
        )
        index += 1
        if end >= len(text):
            break
        next_start = end - overlap
        start = max(start + 1, next_start)
    return chunks


def _semantic_chunks(
    chunks: list[RoutedChunk], module: ModuleDefinition
) -> list[RoutedChunk]:
    terms = [term.lower() for term in [*module.required_sections, *module.keywords]]
    scored: list[tuple[int, int, RoutedChunk]] = []
    for index, chunk in enumerate(chunks):
        section = (chunk.section or "").lower()
        haystack = f"{section}\n{chunk.text}".lower()
        score = sum(4 if term in section else haystack.count(term) for term in terms)
        if index == 0:
            score += 2
        scored.append((score, -index, chunk))
    return [item[2] for item in sorted(scored, reverse=True) if item[0] > 0]


def _disclosure_anchor_chunks(
    chunks: list[RoutedChunk], document: PaperDocument
) -> list[RoutedChunk]:
    offsets = [match.start() for match in DISCLOSURE_HEADINGS.finditer(document.text)]
    for section in document.sections:
        title = section.title.casefold()
        if any(term in title for term in DISCLOSURE_SECTION_TERMS):
            offsets.append(section.start)
    for span in document.spans:
        section = (span.section or "").casefold()
        if section and any(term in section for term in DISCLOSURE_SECTION_TERMS):
            offsets.append(span.start)

    heading_chunks: list[RoutedChunk] = []
    for offset in sorted(set(offsets)):
        containing = [chunk for chunk in chunks if chunk.start <= offset < chunk.end]
        if containing:
            selected = max(containing, key=lambda chunk: chunk.start)
            if selected not in heading_chunks:
                heading_chunks.append(selected)
    front_chunks: list[RoutedChunk] = []
    front_pages: set[int] = set()
    for chunk in chunks:
        if (
            chunk.page is None
            or chunk.page > 3
            or chunk.page in front_pages
            or chunk in heading_chunks
        ):
            continue
        front_pages.add(chunk.page)
        front_chunks.append(chunk)
    return [*heading_chunks, *front_chunks[:2]]


def route_chunks(
    chunks: list[RoutedChunk],
    module: ModuleDefinition,
    limit: int = 8,
    document: PaperDocument | None = None,
) -> list[RoutedChunk]:
    semantic = _semantic_chunks(chunks, module)
    prioritized: list[RoutedChunk] = []
    if module.key == "disclosures" and document:
        prioritized.extend(_disclosure_anchor_chunks(chunks, document))
    prioritized.extend(semantic)
    if not prioritized:
        prioritized.extend(chunks[: min(3, len(chunks))])

    unique: list[RoutedChunk] = []
    seen: set[str] = set()
    for chunk in prioritized:
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        unique.append(chunk)
        if len(unique) >= limit:
            break
    return unique


def format_routed_chunks(chunks: list[RoutedChunk]) -> str:
    return "\n\n".join(
        f"<CHUNK id={chunk.id} page={chunk.page or 'n/a'} "
        f"section={json_label(chunk.section)} start={chunk.start} end={chunk.end}>\n"
        f"{chunk.text}\n</CHUNK>"
        for chunk in chunks
    )


def json_label(value: str | None) -> str:
    if not value:
        return "n/a"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", value)[:80]
