from __future__ import annotations

import re
from dataclasses import dataclass

from sloppy_checker.core.methodology import ModuleDefinition


@dataclass(frozen=True)
class RoutedChunk:
    id: str
    text: str
    start: int
    end: int
    page: int | None
    section: str | None


def chunk_document(text: str, target_chars: int = 4200, overlap: int = 350) -> list[RoutedChunk]:
    page_markers = list(re.finditer(r"\[Page (\d+)\]", text))
    chunks: list[RoutedChunk] = []
    start = 0
    index = 0
    while start < len(text):
        ideal_end = min(len(text), start + target_chars)
        if ideal_end < len(text):
            boundary = max(text.rfind("\n\n", start, ideal_end), text.rfind(". ", start, ideal_end))
            end = boundary + (2 if boundary >= start + target_chars // 2 else 0)
            if end <= start:
                end = ideal_end
        else:
            end = len(text)
        page = None
        for marker in page_markers:
            if marker.start() <= start:
                page = int(marker.group(1))
            else:
                break
        sample = text[start : min(end, start + 300)]
        heading = re.search(r"(?:^|\n)\[?([A-Z][A-Za-z0-9 &/:-]{2,80})\]?\s*(?:\n|$)", sample)
        chunks.append(RoutedChunk(f"chunk-{index}", text[start:end], start, end, page, heading.group(1) if heading else None))
        index += 1
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def route_chunks(chunks: list[RoutedChunk], module: ModuleDefinition, limit: int = 8) -> list[RoutedChunk]:
    terms = [term.lower() for term in [*module.required_sections, *module.keywords]]
    scored: list[tuple[int, int, RoutedChunk]] = []
    for index, chunk in enumerate(chunks):
        haystack = f"{chunk.section or ''}\n{chunk.text}".lower()
        score = sum(4 if term in (chunk.section or "").lower() else haystack.count(term) for term in terms)
        if index == 0:
            score += 2
        scored.append((score, -index, chunk))
    selected = [item[2] for item in sorted(scored, reverse=True) if item[0] > 0][:limit]
    return sorted(selected or chunks[: min(3, len(chunks))], key=lambda chunk: chunk.start)


def format_routed_chunks(chunks: list[RoutedChunk]) -> str:
    return "\n\n".join(
        f"<CHUNK id={chunk.id} page={chunk.page or 'n/a'} start={chunk.start} end={chunk.end}>\n{chunk.text}\n</CHUNK>"
        for chunk in chunks
    )
