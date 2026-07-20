from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from defusedxml import ElementTree as ET

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.ingest import DOI_RE, normalize_doi
from sloppy_checker.core.schemas import (
    ContentCandidate,
    ContentLevel,
    DocumentSection,
    DocumentSpan,
    PaperDocument,
    PaperIdentity,
    ProvenanceRecord,
    ReferenceEntry,
    ResolvedPaper,
    SourceFormat,
)
from sloppy_checker.core.security import validate_public_url

ARXIV_ID_RE = re.compile(
    r"(?:arxiv:\s*)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v\d+)?)",
    re.I,
)
PMCID_RE = re.compile(r"\bPMC(\d+)\b", re.I)
PMID_URL_RE = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/)?(\d{6,9})(?:/|\b)")
ELSEVIER_PII_RE = re.compile(r"(?:PII)?(S\d{4}-\d{4}\(\d{2}\)\d{5}-\d)", re.I)


def _arxiv_identifier(value: str) -> str | None:
    bare = ARXIV_ID_RE.fullmatch(value.strip())
    if bare:
        return bare.group(1)
    if not value.startswith(("http://", "https://")):
        return None
    parsed = urlparse(value)
    if (parsed.hostname or "").casefold() not in {"arxiv.org", "www.arxiv.org"}:
        return None
    path = parsed.path.strip("/")
    for prefix in ("abs/", "pdf/"):
        if path.casefold().startswith(prefix):
            identifier = path[len(prefix) :].removesuffix(".pdf")
            match = ARXIV_ID_RE.fullmatch(identifier)
            return match.group(1) if match else None
    return None


def _strip_markup(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split()) or None


def _candidate(
    fmt: SourceFormat,
    provider: str,
    rank: int,
    url: str | None = None,
    version: str | None = None,
    license_name: str | None = None,
) -> ContentCandidate:
    return ContentCandidate(
        id=hashlib.sha1(f"{provider}:{url}:{fmt}".encode(), usedforsecurity=False).hexdigest()[:16],
        format=fmt,
        url=url,
        version=version,
        license=license_name,
        provider=provider,
        content_level=ContentLevel.FULL_TEXT if fmt in {SourceFormat.PDF, SourceFormat.JATS, SourceFormat.HTML} else ContentLevel.ABSTRACT,
        rank=rank,
    )


class PaperResolver:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": f"sloppy-paper-checker/0.2 (mailto:{settings.ncbi_email})"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _json(
        self, url: str, params: dict | None = None, headers: dict | None = None
    ) -> tuple[dict, str | None]:
        try:
            response = await self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as exc:
            return {}, f"HTTPStatusError:{exc.response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            return {}, type(exc).__name__

    async def resolve(self, raw: str) -> ResolvedPaper:
        value = raw.strip()
        is_url = value.startswith(("http://", "https://"))
        host = urlparse(value).hostname if is_url else None
        # Publisher URLs (not doi.org links) may embed a DOI-shaped substring
        # inside a longer path (e.g. .../articles/10.3389/fpsyg.2020.00087/full),
        # which DOI_RE cannot reliably distinguish from a real DOI suffix. Let
        # _resolve_url scrape the page's own citation_doi meta tag instead of
        # trusting a regex match against the raw URL.
        allow_doi_shortcut = not is_url or (host is not None and host.lower().endswith("doi.org"))
        doi = None
        if allow_doi_shortcut:
            try:
                doi = normalize_doi(value)
            except ValueError:
                doi = None
        arxiv = _arxiv_identifier(value)
        pmcid = PMCID_RE.search(value)
        pmid = PMID_URL_RE.search(value) if not pmcid else None
        if doi:
            return await self._resolve_doi(doi)
        if pmcid:
            return await self._resolve_ncbi("PMC" + pmcid.group(1))
        if pmid and (value.isdigit() or "pubmed" in value.lower()):
            return await self._resolve_ncbi(pmid.group(1))
        if arxiv:
            return await self._resolve_arxiv(arxiv)
        if is_url:
            return await self._resolve_url(value)
        raise ValueError("Enter a DOI, arXiv ID, PMID, PMCID, or absolute scholarly URL")

    def _finish(
        self,
        identity: PaperIdentity,
        abstract: str | None,
        candidates: list[ContentCandidate],
        provenance: list[ProvenanceRecord],
        limitations: list[str],
    ) -> ResolvedPaper:
        candidates = sorted({candidate.id: candidate for candidate in candidates}.values(), key=lambda item: item.rank)
        level = ContentLevel.FULL_TEXT if any(c.content_level == ContentLevel.FULL_TEXT for c in candidates) else ContentLevel.ABSTRACT if abstract else ContentLevel.METADATA
        return ResolvedPaper(
            id=uuid4(),
            identity=identity,
            abstract=abstract,
            content_level=level,
            candidates=candidates,
            provenance=provenance,
            limitations=limitations,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.resolution_ttl_seconds),
        )

    async def _resolve_doi(self, doi: str) -> ResolvedPaper:
        now = datetime.now(UTC)
        crossref, cr_error = await self._json(f"https://api.crossref.org/works/{doi}")
        message = crossref.get("message", {})
        identity = PaperIdentity(
            doi=doi,
            title=(message.get("title") or [None])[0],
            authors=[" ".join(filter(None, [item.get("given"), item.get("family")])) for item in message.get("author", [])],
            journal=(message.get("container-title") or [None])[0],
            published_at=str((message.get("published") or {}).get("date-parts", [[None]])[0][0] or "") or None,
        )
        abstract = _strip_markup(message.get("abstract"))
        provenance = [ProvenanceRecord(provider="Crossref", available=not cr_error, detail=cr_error, accessed_at=now)]
        limitations = ["Crossref metadata unavailable."] if cr_error else []
        candidates: list[ContentCandidate] = []
        unpaywall, up_error = await self._json(
            f"https://api.unpaywall.org/v2/{doi}", {"email": self.settings.unpaywall_email}
        )
        provenance.append(ProvenanceRecord(provider="Unpaywall", available=not up_error, detail=up_error, accessed_at=now))
        for location in unpaywall.get("oa_locations", []) if not up_error else []:
            pdf_url = location.get("url_for_pdf")
            if pdf_url:
                version = location.get("version")
                version_rank = {"publishedVersion": 10, "acceptedVersion": 20, "submittedVersion": 30}.get(version, 35)
                candidates.append(_candidate(SourceFormat.PDF, "Unpaywall", version_rank, pdf_url, version, location.get("license")))
        identity.versions = sorted(
            {
                str(location.get("version"))
                for location in unpaywall.get("oa_locations", [])
                if location.get("version")
            }
        )
        ids, id_error = await self._json(
            "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
            {"ids": doi, "format": "json", "tool": "sloppy-paper-checker", "email": self.settings.ncbi_email},
        )
        provenance.append(ProvenanceRecord(provider="NCBI", available=not id_error, detail=id_error, accessed_at=now))
        record = (ids.get("records") or [{}])[0]
        if record.get("pmcid"):
            identity.pmcid = record["pmcid"]
            identity.pmid = str(record.get("pmid") or "") or None
            candidates.append(_candidate(SourceFormat.JATS, "PMC", 25, self._pmc_jats_url(record["pmcid"]), "publishedVersion"))
            candidates.append(_candidate(SourceFormat.HTML, "PMC", 26, self._pmc_html_url(record["pmcid"]), "publishedVersion"))
        return self._finish(identity, abstract, candidates, provenance, limitations)

    async def _resolve_arxiv(self, arxiv_id: str) -> ResolvedPaper:
        now = datetime.now(UTC)
        clean = re.sub(r"v\d+$", "", arxiv_id)
        try:
            response = await self.client.get("https://export.arxiv.org/api/query", params={"id_list": clean})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
            entry = root.find("atom:entry", ns)
            if entry is None:
                raise ValueError("arXiv record was not found")
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns)).split())
            abstract = " ".join((entry.findtext("atom:summary", default="", namespaces=ns)).split())
            authors = [node.findtext("atom:name", default="", namespaces=ns) for node in entry.findall("atom:author", ns)]
            doi = entry.findtext("arxiv:doi", default="", namespaces=ns) or None
            identity = PaperIdentity(arxiv_id=clean, doi=doi, title=title, authors=authors, published_at=entry.findtext("atom:published", default=None, namespaces=ns), updated_at=entry.findtext("atom:updated", default=None, namespaces=ns))
            candidates = [_candidate(SourceFormat.PDF, "arXiv", 30, f"https://arxiv.org/pdf/{clean}", "submittedVersion")]
            return self._finish(identity, abstract, candidates, [ProvenanceRecord(provider="arXiv", accessed_at=now)], [])
        except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
            identity = PaperIdentity(arxiv_id=clean)
            return self._finish(identity, None, [], [ProvenanceRecord(provider="arXiv", available=False, detail=type(exc).__name__, accessed_at=now)], ["arXiv metadata unavailable."])

    @staticmethod
    def _pmc_jats_url(pmcid: str) -> str:
        number = pmcid.upper().removeprefix("PMC")
        return f"https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?verb=GetRecord&identifier=oai:pubmedcentral.nih.gov:{number}&metadataPrefix=pmc"

    @staticmethod
    def _pmc_html_url(pmcid: str) -> str:
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid.upper()}/"

    async def _resolve_ncbi(self, identifier: str) -> ResolvedPaper:
        now = datetime.now(UTC)
        ids, id_error = await self._json(
            "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
            {"ids": identifier, "format": "json", "tool": "sloppy-paper-checker", "email": self.settings.ncbi_email},
        )
        record = (ids.get("records") or [{}])[0]
        pmid = str(record.get("pmid") or (identifier if identifier.isdigit() else "")) or None
        pmcid = record.get("pmcid") or (identifier.upper() if identifier.upper().startswith("PMC") else None)
        doi = record.get("doi")
        identity = PaperIdentity(doi=doi, pmid=pmid, pmcid=pmcid)
        abstract = None
        limitations: list[str] = []
        if pmid:
            try:
                response = await self.client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params={"db": "pubmed", "id": pmid, "retmode": "xml", "tool": "sloppy-paper-checker", "email": self.settings.ncbi_email},
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
                identity.title = " ".join("".join(root.find(".//ArticleTitle").itertext()).split()) if root.find(".//ArticleTitle") is not None else None
                identity.authors = [" ".join(filter(None, [node.findtext("ForeName"), node.findtext("LastName")])) for node in root.findall(".//Author")]
                identity.journal = root.findtext(".//Journal/Title")
                abstract = " ".join(" ".join(node.itertext()) for node in root.findall(".//AbstractText")) or None
            except (httpx.HTTPError, ET.ParseError):
                limitations.append("PubMed metadata unavailable.")
        candidates = [
            _candidate(SourceFormat.JATS, "PMC", 25, self._pmc_jats_url(pmcid), "publishedVersion"),
            _candidate(SourceFormat.HTML, "PMC", 26, self._pmc_html_url(pmcid), "publishedVersion"),
        ] if pmcid else []
        return self._finish(identity, abstract, candidates, [ProvenanceRecord(provider="NCBI", available=not id_error, detail=id_error, accessed_at=now)], limitations)

    async def _resolve_url(self, url: str) -> ResolvedPaper:
        validate_public_url(url)
        parsed = urlparse(url)
        if parsed.hostname and (
            parsed.hostname.lower().endswith("thelancet.com")
            or parsed.hostname.lower().endswith("sciencedirect.com")
        ):
            pii = ELSEVIER_PII_RE.search(parsed.path)
            if pii:
                return await self._resolve_doi(f"10.1016/{pii.group(1)}".lower())
        if parsed.path.lower().endswith(".pdf") or "/pdf/" in parsed.path.lower():
            return self._finish(PaperIdentity(), None, [_candidate(SourceFormat.PDF, parsed.hostname or "URL", 40, url)], [ProvenanceRecord(provider="Direct URL", accessed_at=datetime.now(UTC))], [])
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("Publisher-page redirects are not followed")
            if len(response.content) > 2_000_000:
                raise ValueError("Publisher page is too large")
            html = response.text
            def meta(name: str) -> str | None:
                match = re.search(rf"<meta[^>]+(?:name|property)=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']+)", html, re.I)
                if not match:
                    match = re.search(rf"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:name|property)=[\"']{re.escape(name)}[\"']", html, re.I)
                return unescape(match.group(1)).strip() if match else None
            doi_raw = next((meta(name) for name in ("citation_doi", "DC.Identifier", "dc.identifier", "prism.doi") if meta(name)), None)
            if doi_raw and DOI_RE.search(doi_raw):
                return await self._resolve_doi(normalize_doi(doi_raw))
            pdf_url = meta("citation_pdf_url")
            identity = PaperIdentity(title=meta("citation_title") or meta("og:title"))
            candidates = [_candidate(SourceFormat.PDF, parsed.hostname or "Publisher", 40, urljoin(url, pdf_url))] if pdf_url else []
            abstract = meta("citation_abstract") or meta("description") or meta("og:description")
            if not any(
                [identity.title, identity.authors, identity.journal, identity.doi, abstract, candidates]
            ):
                raise ValueError(
                    "The publisher page did not expose a canonical paper record. "
                    "Enter its DOI, PMID, or PMCID instead."
                )
            return self._finish(identity, abstract, candidates, [ProvenanceRecord(provider=parsed.hostname or "Publisher", accessed_at=datetime.now(UTC))], [])
        except httpx.HTTPError as exc:
            raise ValueError(
                "The publisher page could not be accessed. Enter the paper's DOI, PMID, or PMCID instead."
            ) from exc


async def fetch_bounded_pdf(url: str, settings: AppSettings) -> bytes:
    current = validate_public_url(url)
    async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds, follow_redirects=False) as client:
        for _ in range(4):
            async with client.stream(
                "GET", current, headers={"User-Agent": "sloppy-paper-checker/0.2"}
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Remote PDF redirect had no destination")
                    current = validate_public_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                declared = int(response.headers.get("content-length", "0") or 0)
                if declared > settings.max_upload_bytes:
                    raise ValueError("Remote PDF exceeds the configured size limit")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > settings.max_upload_bytes:
                        raise ValueError("Remote PDF exceeds the configured size limit")
                content_type = response.headers.get("content-type", "").lower()
                if not data.startswith(b"%PDF-") or (
                    content_type and "pdf" not in content_type and "octet-stream" not in content_type
                ):
                    raise ValueError("Resolved artifact is not a PDF")
                return bytes(data)
    raise ValueError("Remote PDF exceeded the redirect limit")


async def fetch_jats_document(candidate: ContentCandidate, identity: PaperIdentity, settings: AppSettings) -> PaperDocument:
    if not candidate.url:
        raise ValueError("JATS candidate has no URL")
    url = validate_public_url(str(candidate.url))
    async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds, follow_redirects=False) as client:
        response = await client.get(url, headers={"User-Agent": f"sloppy-paper-checker/0.2 (mailto:{settings.ncbi_email})"})
        response.raise_for_status()
    if len(response.content) > settings.max_upload_bytes:
        raise ValueError("JATS document exceeds the configured size limit")
    root = ET.fromstring(response.content)
    for node in root.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.rsplit("}", 1)[-1]
    article = root.find(".//article")
    if article is None:
        raise ValueError("PMC response did not contain JATS full text")
    pieces: list[str] = []
    spans: list[DocumentSpan] = []
    sections: list[DocumentSection] = []
    references: list[ReferenceEntry] = []
    extraction_warnings: list[str] = []
    position = 0

    def node_text(node) -> str:
        return " ".join(" ".join(node.itertext()).split())

    def append_section(title: str, paragraphs: list[str], section_id: str) -> None:
        nonlocal position
        cleaned = list(dict.fromkeys(paragraph for paragraph in paragraphs if paragraph))
        if not cleaned:
            return
        section_start = position
        heading = f"[{title}]\n"
        pieces.append(heading)
        position += len(heading)
        for paragraph_index, paragraph_text in enumerate(cleaned):
            start = position
            rendered = paragraph_text + "\n\n"
            pieces.append(rendered)
            position += len(rendered)
            spans.append(
                DocumentSpan(
                    id=f"{section_id}-p-{paragraph_index}",
                    text=paragraph_text,
                    start=start,
                    end=start + len(paragraph_text),
                    section=title,
                    paragraph=f"{section_id}-p-{paragraph_index}",
                )
            )
        sections.append(
            DocumentSection(id=section_id, title=title, start=section_start, end=position)
        )

    article_meta = article.find("./front/article-meta")
    if article_meta is None:
        article_meta = article.find(".//front/article-meta")
    if article_meta is not None:
        metadata_lines: list[str] = []
        title_node = article_meta.find(".//article-title")
        if title_node is not None and node_text(title_node):
            metadata_lines.append("Title: " + node_text(title_node))
        journal_node = article.find(".//front/journal-meta//journal-title")
        if journal_node is not None and node_text(journal_node):
            metadata_lines.append("Journal: " + node_text(journal_node))
        for article_id in article_meta.findall("article-id"):
            value = node_text(article_id)
            if value:
                label = article_id.get("pub-id-type", "identifier").upper()
                metadata_lines.append(f"{label}: {value}")
        append_section("Article metadata", metadata_lines, "front-metadata")

        abstract_paragraphs: list[str] = []
        for abstract in article_meta.findall("abstract"):
            paragraphs = [node_text(item) for item in abstract.findall(".//p")]
            abstract_paragraphs.extend(paragraphs or [node_text(abstract)])
        append_section("Abstract", abstract_paragraphs, "front-abstract")

        affiliations = [node_text(node) for node in article_meta.findall(".//aff")]
        append_section("Affiliations", affiliations, "front-affiliations")

        front_funding = [node_text(node) for node in article_meta.findall(".//funding-group")]
        append_section("Funding", front_funding, "front-funding")

        author_notes = list(
            dict.fromkeys(
                node_text(child)
                for notes in article_meta.findall(".//author-notes")
                for child in notes
                if child.tag in {"fn", "p"} and node_text(child)
            )
        )
        append_section("Author notes and conflicts", author_notes, "front-author-notes")

        custom_disclosures: list[str] = []
        for custom_meta in article_meta.findall(".//custom-meta"):
            name = node_text(custom_meta.find("meta-name")) if custom_meta.find("meta-name") is not None else ""
            if any(term in name.casefold() for term in ("fund", "conflict", "competing", "data avail")):
                custom_disclosures.append(node_text(custom_meta))
        append_section("Additional disclosures", custom_disclosures, "front-disclosures")

    body_sections = article.findall("./body//sec")
    for section_index, sec in enumerate(body_sections):
        title_node = sec.find("title")
        title = node_text(title_node) if title_node is not None else f"Section {section_index + 1}"
        append_section(
            title,
            [node_text(paragraph) for paragraph in sec.findall("p")],
            f"body-sec-{section_index}",
        )

    for section_index, sec in enumerate(article.findall("./back//sec")):
        title_node = sec.find("title")
        title = node_text(title_node) if title_node is not None else f"Back matter {section_index + 1}"
        append_section(
            title,
            [node_text(paragraph) for paragraph in sec.findall("p")],
            f"back-sec-{section_index}",
        )

    acknowledgments = [node_text(node) for node in article.findall("./back//ack")]
    append_section("Acknowledgments", acknowledgments, "back-acknowledgments")

    back_funding = [node_text(node) for node in article.findall("./back//funding-group")]
    append_section("Funding", back_funding, "back-funding")

    back_notes: list[str] = []
    for note in article.findall("./back//fn"):
        note_type = (note.get("fn-type") or "").casefold()
        if any(term in note_type for term in ("conflict", "coi", "competing", "financial")):
            back_notes.append(node_text(note))
    append_section("Conflicts of interest", back_notes, "back-conflicts")

    captions: list[str] = []
    for wrapper in [*article.findall(".//fig"), *article.findall(".//table-wrap")]:
        caption = wrapper.find("caption")
        if caption is not None and node_text(caption):
            label = wrapper.find("label")
            prefix = node_text(label) + ": " if label is not None and node_text(label) else ""
            captions.append(prefix + node_text(caption))
    append_section("Figure and table captions", captions, "captions")

    supplements: list[str] = []
    supplement_links = False
    for supplement in article.findall(".//supplementary-material"):
        label = supplement.find("label")
        caption = supplement.find("caption")
        text_parts = [
            node_text(node)
            for node in (label, caption)
            if node is not None and node_text(node)
        ]
        if text_parts:
            supplements.append(" ".join(text_parts))
        supplement_links = supplement_links or any(
            key.rsplit("}", 1)[-1] == "href"
            for node in supplement.iter()
            for key in node.attrib
        )
    append_section("Supplementary material", supplements, "supplements")
    if article.findall(".//supplementary-material") and supplement_links:
        extraction_warnings.append(
            "Supplementary files were linked by the article, but their file contents were not parsed."
        )
    for reference_index, reference in enumerate(article.findall(".//ref-list//ref")):
        raw = " ".join(" ".join(reference.itertext()).split())
        if raw:
            doi_match = DOI_RE.search(raw)
            references.append(
                ReferenceEntry(
                    id=reference.get("id") or f"ref-{reference_index}",
                    raw=raw,
                    doi=doi_match.group(1).lower() if doi_match else None,
                )
            )
    text = "".join(pieces)
    if not text.strip():
        raise ValueError("No analyzable JATS article text was found")
    digest = hashlib.sha256(response.content).hexdigest()
    return PaperDocument(
        identity=identity,
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.JATS,
        sha256=digest,
        parser_name="pmc-jats",
        parser_version="1.2",
        text=text,
        spans=spans,
        sections=sections,
        references=references,
        extraction_warnings=extraction_warnings,
    )
class _PmcArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.article_depth = 0
        self.current_tag: str | None = None
        self.current_text: list[str] = []
        self.section = "Article"
        self.paragraphs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self.article_depth += 1
        elif self.article_depth and self.current_tag is None and tag in {"h1", "h2", "h3", "p", "li", "figcaption"}:
            self.current_tag = tag
            self.current_text = []
        elif self.article_depth and self.current_tag is not None and tag == "br":
            self.current_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            self.article_depth -= 1
            return
        if not self.article_depth or tag != self.current_tag:
            return
        text = " ".join("".join(self.current_text).split())
        if text:
            if tag in {"h1", "h2", "h3"}:
                self.section = text
            else:
                self.paragraphs.append((self.section, text))
        self.current_tag = None
        self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.article_depth and self.current_tag is not None:
            self.current_text.append(data + " ")


async def fetch_pmc_html_document(candidate: ContentCandidate, identity: PaperIdentity, settings: AppSettings) -> PaperDocument:
    if not candidate.url:
        raise ValueError("PMC HTML candidate has no URL")
    url = validate_public_url(str(candidate.url))
    async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds, follow_redirects=False) as client:
        response = await client.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "User-Agent": "Mozilla/5.0",
            },
        )
        response.raise_for_status()
    if len(response.content) > settings.max_upload_bytes:
        raise ValueError("PMC HTML document exceeds the configured size limit")
    if "html" not in response.headers.get("content-type", "").lower():
        raise ValueError("PMC response is not HTML")
    parser = _PmcArticleParser()
    parser.feed(response.text)
    parser.close()
    if not parser.paragraphs:
        raise ValueError("PMC HTML response did not contain full text")
    pieces: list[str] = []
    spans: list[DocumentSpan] = []
    sections: list[DocumentSection] = []
    position = 0

    def append_section(title: str, paragraphs: list[str], section_id: str) -> None:
        nonlocal position
        cleaned = list(dict.fromkeys(paragraphs))
        if not cleaned:
            return
        section_start = position
        heading = f"[{title}]\n"
        pieces.append(heading)
        position += len(heading)
        for paragraph_index, paragraph in enumerate(cleaned):
            start = position
            pieces.append(paragraph + "\n\n")
            position += len(paragraph) + 2
            spans.append(DocumentSpan(
                id=f"{section_id}-p-{paragraph_index}",
                text=paragraph,
                start=start,
                end=start + len(paragraph),
                section=title,
                paragraph=f"{section_id}-p-{paragraph_index}",
            ))
        sections.append(DocumentSection(id=section_id, title=title, start=section_start, end=position))

    section_title = parser.paragraphs[0][0]
    section_paragraphs: list[str] = []
    section_index = 0
    for title, paragraph in parser.paragraphs:
        if title != section_title:
            append_section(section_title, section_paragraphs, f"pmc-html-{section_index}")
            section_index += 1
            section_title = title
            section_paragraphs = []
        section_paragraphs.append(paragraph)
    append_section(section_title, section_paragraphs, f"pmc-html-{section_index}")
    text = "".join(pieces)
    if not text.strip():
        raise ValueError("No analyzable PMC HTML article text was found")
    return PaperDocument(
        identity=identity,
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.HTML,
        sha256=hashlib.sha256(response.content).hexdigest(),
        parser_name="pmc-html",
        parser_version="1.0",
        text=text,
        spans=spans,
        sections=sections,
    )


async def fetch_pmc_document(candidate: ContentCandidate, identity: PaperIdentity, settings: AppSettings) -> PaperDocument:
    if candidate.provider != "PMC":
        raise ValueError("This full-text source is not provided by PMC")
    if candidate.format == SourceFormat.JATS:
        return await fetch_jats_document(candidate, identity, settings)
    if candidate.format == SourceFormat.HTML:
        return await fetch_pmc_html_document(candidate, identity, settings)
    raise ValueError("This artifact is not PMC full text")
