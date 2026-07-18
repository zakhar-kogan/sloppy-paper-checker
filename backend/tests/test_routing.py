import hashlib
import math

from sloppy_checker.core.methodology import load_methodology
from sloppy_checker.core.schemas import (
    ContentLevel,
    DocumentPage,
    DocumentSection,
    DocumentSpan,
    PaperDocument,
    SourceFormat,
)
from sloppy_checker.workflows.routing import chunk_document, route_chunks


def document_with_pages(page_count: int = 11) -> PaperDocument:
    pages = []
    text = ""
    for number in range(1, page_count + 1):
        page_text = (f"Page {number} methods results discussion. " * 145).strip() + "\n\n"
        if number == 2:
            page_text += "Department of Psychiatry, Example University.\n\n"
        if number == 5:
            page_text += "Role of the funding source\nThe funder had no role in analysis.\n\n"
        if number == 11:
            page_text += "Declaration of interests\nThe authors declare no competing interests.\n"
        start = len(text)
        text += page_text
        pages.append(DocumentPage(number=number, text=page_text, start=start, end=len(text)))
    return PaperDocument(
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.PDF,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_name="test",
        parser_version="1",
        text=text,
        pages=pages,
    )


def test_chunking_is_bounded_and_preserves_page_locations():
    document = document_with_pages()
    chunks = chunk_document(document, target_chars=4200, overlap=350)
    expected = math.ceil(len(document.text) / (4200 - 350))
    assert expected <= len(chunks) <= expected + 2
    assert all(right.start - left.start >= 1750 for left, right in zip(chunks, chunks[1:], strict=False))
    assert {chunk.page for chunk in chunks if chunk.page} >= {1, 5, 11}


def test_disclosure_routing_reserves_front_matter_funding_and_conflicts():
    document = document_with_pages()
    chunks = chunk_document(document)
    module = next(item for item in load_methodology().definition.modules if item.key == "disclosures")
    routed = route_chunks(chunks, module, limit=8, document=document)
    routed_text = "\n".join(item.text for item in routed)
    assert "Example University" in routed_text
    assert "Role of the funding source" in routed_text
    assert "Declaration of interests" in routed_text


def test_inline_jats_disclosures_are_prioritized_without_interpreting_them():
    front = "Authors and affiliations\nDepartment of Psychiatry, Example University.\n\n"
    methods = ("Methods and benchmark results. " * 180) + "\n\n"
    disclosures = (
        "Conflict of Interest Disclosures: Dr Example reported consulting fees.\n\n"
        "Funding/Support: Supported by Example Foundation.\n\n"
    )
    text = front + methods + disclosures
    document = PaperDocument(
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.JATS,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_name="test-jats",
        parser_version="1",
        text=text,
        sections=[
            DocumentSection(id="body", title="Methods", start=len(front), end=len(front + methods)),
            DocumentSection(id="coi", title="Conflict of Interest Disclosures", start=len(front + methods), end=len(text)),
        ],
        spans=[
            DocumentSpan(id="aff-1", text=front.strip(), start=0, end=len(front), section="Affiliations"),
            DocumentSpan(id="coi-1", text=disclosures.strip(), start=len(front + methods), end=len(text), section="Conflict of Interest Disclosures"),
        ],
    )
    chunks = chunk_document(document, target_chars=1200, overlap=100)
    module = next(item for item in load_methodology().definition.modules if item.key == "disclosures")
    routed = route_chunks(chunks, module, limit=4, document=document)

    assert any("Conflict of Interest Disclosures:" in chunk.text for chunk in routed[:2])
    assert any("Example University" in chunk.text for chunk in routed[:3])
    assert len([chunk for chunk in routed if chunk.page is not None and chunk.page <= 3]) <= 2
