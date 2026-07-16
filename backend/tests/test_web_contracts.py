import hashlib
import json
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.methodology import load_methodology
from sloppy_checker.core.schemas import (
    AnalysisRequest,
    ContentCandidate,
    ContentLevel,
    DocumentSpan,
    PaperDocument,
    PaperIdentity,
    SourceFormat,
)
from sloppy_checker.evidence import resolver as resolver_module
from sloppy_checker.evidence.resolver import fetch_bounded_pdf, fetch_jats_document
from sloppy_checker.main import app
from sloppy_checker.workflows.routing import chunk_document, route_chunks


def metadata_document() -> dict:
    text = "Title: A metadata-only test paper"
    return {
        "schema_version": "1.0",
        "identity": {"title": "A metadata-only test paper"},
        "content_level": "metadata",
        "source_format": "metadata",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parser_name": "test",
        "parser_version": "1",
        "text": text,
        "spans": [{"id": "metadata", "text": text, "start": 0, "end": len(text)}],
    }


def test_anonymous_session_owns_document_and_report():
    with TestClient(app) as owner, TestClient(app) as stranger:
        session = owner.post("/v1/session")
        assert session.status_code == 200
        assert session.cookies.get("spc_guest")
        receipt = owner.post("/v1/documents", json=metadata_document())
        assert receipt.status_code == 201
        analysis = owner.post(
            "/v1/analyses",
            json={"source": {"kind": "document", "value": receipt.json()["id"]}},
        )
        assert analysis.status_code == 202
        analysis_id = analysis.json()["id"]
        assert owner.get(f"/v1/analyses/{analysis_id}/report").status_code == 200
        stranger.post("/v1/session")
        assert stranger.get(f"/v1/analyses/{analysis_id}/report").status_code == 404


def test_session_refresh_keeps_the_same_anonymous_owner():
    with TestClient(app) as client:
        client.post("/v1/session")
        original = client.cookies.get("spc_guest").split(".", 1)[0]
        client.post("/v1/session")
        assert client.cookies.get("spc_guest").split(".", 1)[0] == original


def test_paper_document_rejects_out_of_bounds_anchors():
    with pytest.raises(ValueError, match="anchor exceeds"):
        PaperDocument(
            identity=PaperIdentity(),
            content_level=ContentLevel.FULL_TEXT,
            source_format=SourceFormat.PDF,
            sha256="0" * 64,
            parser_name="pdf.js",
            parser_version="test",
            text="short",
            spans=[DocumentSpan(id="bad", text="short", start=0, end=99)],
        )


def test_experimental_markdown_cannot_be_a_canonical_source_format():
    payload = metadata_document()
    payload["source_format"] = "markdown"
    with pytest.raises(ValueError):
        PaperDocument.model_validate(payload)


def test_analysis_contract_rejects_runtime_provider_credentials():
    payload = {
        "source": {"kind": "document", "value": "00000000-0000-0000-0000-000000000000"},
        "provider": {
            "mode": "byok",
            "profile": "openai",
            "api_key": "test-key-value",
            "worker_model": "worker",
        },
    }
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        AnalysisRequest.model_validate(payload)


def test_methodology_bundle_is_hashed_and_routing_is_deterministic():
    bundle = load_methodology()
    assert len(bundle.bundle_hash) == 64
    chunks = chunk_document("Methods\nRandomization and blinding.\n\nResults\nAn effect was observed." * 200)
    module = bundle.definition.modules[0]
    first = route_chunks(chunks, module)
    assert [chunk.id for chunk in first] == [chunk.id for chunk in route_chunks(chunks, module)]
    assert len(first) <= bundle.definition.routing.max_chunks_per_module


@pytest.mark.asyncio
@respx.mock
async def test_bounded_pdf_checks_magic_and_size(monkeypatch):
    monkeypatch.setattr(resolver_module, "validate_public_url", lambda url: url)
    url = "https://papers.example/test.pdf"
    respx.get(url).mock(return_value=Response(200, content=b"not-pdf", headers={"content-type": "application/pdf"}))
    with pytest.raises(ValueError, match="not a PDF"):
        await fetch_bounded_pdf(url, AppSettings(max_upload_bytes=100))


@pytest.mark.asyncio
@respx.mock
async def test_doi_resolution_ranks_pdf_versions_before_jats():
    resolver = resolver_module.PaperResolver(AppSettings())
    doi = "10.5555/resolution.1"
    respx.get(f"https://api.crossref.org/works/{doi}").mock(
        return_value=Response(200, json={"message": {"title": ["Resolved paper"]}})
    )
    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=Response(
            200,
            json={
                "oa_locations": [
                    {"url_for_pdf": "https://example.org/submitted.pdf", "version": "submittedVersion"},
                    {"url_for_pdf": "https://example.org/published.pdf", "version": "publishedVersion"},
                    {"url_for_pdf": "https://example.org/published.pdf", "version": "publishedVersion"},
                ]
            },
        )
    )
    respx.get("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/").mock(
        return_value=Response(200, json={"records": [{"pmcid": "PMC123", "pmid": "123"}]})
    )
    try:
        resolved = await resolver.resolve(doi)
    finally:
        await resolver.close()
    assert [candidate.format for candidate in resolved.candidates] == [
        SourceFormat.PDF,
        SourceFormat.PDF,
        SourceFormat.JATS,
    ]
    assert resolved.identity.versions == ["publishedVersion", "submittedVersion"]


@pytest.mark.asyncio
@respx.mock
async def test_lancet_fixture_preserves_version_license_and_provider_provenance():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "lancet_2017_32802.json").read_text()
    )
    doi = fixture["doi"].lower()
    respx.get(f"https://api.crossref.org/works/{doi}").mock(
        return_value=Response(200, json=fixture["crossref"])
    )
    respx.get(f"https://api.unpaywall.org/v2/{doi}").mock(
        return_value=Response(200, json=fixture["unpaywall"])
    )
    respx.get("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/").mock(
        return_value=Response(200, json=fixture["ncbi"])
    )
    resolver = resolver_module.PaperResolver(AppSettings())
    try:
        resolved = await resolver.resolve(doi)
    finally:
        await resolver.close()
    assert resolved.identity.journal == "The Lancet"
    assert [candidate.version for candidate in resolved.candidates] == [
        "publishedVersion",
        "submittedVersion",
        "publishedVersion",
    ]
    assert resolved.candidates[0].license == "cc-by"
    assert [record.provider for record in resolved.provenance] == [
        "Crossref",
        "Unpaywall",
        "NCBI",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_provider", ["crossref", "unpaywall", "ncbi"])
@respx.mock
async def test_doi_providers_fail_independently(failed_provider):
    doi = "10.5555/provider.failure"
    endpoints = {
        "crossref": f"https://api.crossref.org/works/{doi}",
        "unpaywall": f"https://api.unpaywall.org/v2/{doi}",
        "ncbi": "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
    }
    good = {
        "crossref": {"message": {"title": ["Still resolvable"]}},
        "unpaywall": {
            "oa_locations": [
                {
                    "url_for_pdf": "https://example.org/paper.pdf",
                    "version": "acceptedVersion",
                    "license": "cc-by",
                }
            ]
        },
        "ncbi": {"records": [{"pmcid": "PMC123", "pmid": "123"}]},
    }
    for provider, endpoint in endpoints.items():
        respx.get(endpoint).mock(
            return_value=Response(503) if provider == failed_provider else Response(200, json=good[provider])
        )
    resolver = resolver_module.PaperResolver(AppSettings())
    try:
        resolved = await resolver.resolve(doi)
    finally:
        await resolver.close()
    availability = {record.provider.casefold(): record.available for record in resolved.provenance}
    assert availability[failed_provider] is False
    assert sum(availability.values()) == 2


@pytest.mark.asyncio
@respx.mock
async def test_pubmed_url_is_resolved_as_pmid_before_arxiv_pattern():
    resolver = resolver_module.PaperResolver(AppSettings())
    respx.get("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/").mock(
        return_value=Response(
            200,
            json={
                "records": [
                    {
                        "pmid": "41366844",
                        "pmcid": "PMC12910469",
                        "doi": "10.1176/appi.ajp.20241115",
                    }
                ]
            },
        )
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=Response(
            200,
            content=b"<PubmedArticle><Article><ArticleTitle>Resolved review</ArticleTitle>"
            b"<Abstract><AbstractText>Abstract text.</AbstractText></Abstract>"
            b"</Article></PubmedArticle>",
        )
    )
    try:
        resolved = await resolver.resolve("https://pubmed.ncbi.nlm.nih.gov/41366844/")
    finally:
        await resolver.close()
    assert resolved.identity.pmid == "41366844"
    assert resolved.identity.pmcid == "PMC12910469"
    assert resolved.identity.doi == "10.1176/appi.ajp.20241115"


@pytest.mark.asyncio
@respx.mock
async def test_namespaced_pmc_jats_produces_stable_paragraph_anchors(monkeypatch):
    monkeypatch.setattr(resolver_module, "validate_public_url", lambda url: url)
    url = "https://pmc.example/oai"
    xml = b"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record><metadata>
      <article xmlns="http://jats.nlm.nih.gov" xmlns:xlink="http://www.w3.org/1999/xlink">
      <front><journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
      <article-meta><article-id pub-id-type="doi">10.1234/TEST.1</article-id><title-group><article-title>Test article</article-title></title-group>
      <abstract><p>Structured abstract text.</p></abstract><contrib-group><aff id="A1">Example University</aff></contrib-group>
      <funding-group><award-group><funding-source>Example Funder</funding-source></award-group></funding-group>
      <author-notes><fn fn-type="conflict"><p>No competing interests.</p></fn></author-notes></article-meta></front>
      <body><sec><title>Methods</title><p>Participants were randomly assigned.</p>
      <fig><label>Figure 1</label><caption><p>Participant flow.</p></caption></fig></sec></body>
      <back><ack><p>The sponsor had no role in the study.</p></ack>
      <supplementary-material xlink:href="supplement.pdf"><caption><p>Supplemental methods.</p></caption></supplementary-material><ref-list>
      <ref id="R1"><mixed-citation>Example doi:10.1234/TEST.1</mixed-citation></ref>
      </ref-list></back></article></metadata></record></GetRecord></OAI-PMH>"""
    respx.get(url).mock(return_value=Response(200, content=xml))
    candidate = ContentCandidate(
        id="candidate",
        format=SourceFormat.JATS,
        url=url,
        provider="PMC",
        content_level=ContentLevel.FULL_TEXT,
        rank=1,
    )
    document = await fetch_jats_document(candidate, PaperIdentity(pmcid="PMC1"), AppSettings())
    assert document.content_level == ContentLevel.FULL_TEXT
    assert any(span.paragraph == "body-sec-0-p-0" for span in document.spans)
    assert "[Affiliations]\nExample University" in document.text
    assert "[Funding]\nExample Funder" in document.text
    assert "[Author notes and conflicts]\nNo competing interests." in document.text
    assert "[Acknowledgments]\nThe sponsor had no role in the study." in document.text
    assert "[Figure and table captions]\nFigure 1: Participant flow." in document.text
    assert "[Supplementary material]\nSupplemental methods." in document.text
    assert document.extraction_warnings == [
        "Supplementary files were linked by the article, but their file contents were not parsed."
    ]
    assert document.parser_version == "1.1"
    assert document.references[0].doi == "10.1234/test.1"
