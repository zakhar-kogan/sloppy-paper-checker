import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from sloppy_checker.api import routes as routes_module
from sloppy_checker.core.config import get_settings
from sloppy_checker.core.database import AnalysisRow, DocumentRow, SessionLocal
from sloppy_checker.core.repository import ResolutionRepository
from sloppy_checker.core.schemas import (
    ContentCandidate,
    ContentLevel,
    PaperDocument,
    PaperIdentity,
    ResolvedPaper,
    SourceFormat,
)
from sloppy_checker.core.storage import get_document_store
from sloppy_checker.main import app

AUTH = {"Authorization": "Bearer development-only-change-me"}


def document_payload(title: str = "A canonical test paper") -> dict:
    text = f"Title: {title}\n\nMethods and participants. Results and discussion."
    return {
        "identity": {"title": title},
        "content_level": "full_text",
        "source_format": "pdf",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parser_name": "pdf.js",
        "parser_version": "test",
        "text": text,
        "spans": [{"id": "all", "text": text, "start": 0, "end": len(text)}],
    }


def test_health_and_removed_legacy_routes():
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/settings", headers=AUTH).status_code == 404
        assert client.post("/v1/uploads", headers=AUTH).status_code == 404


def test_document_is_stored_behind_an_opaque_key():
    with TestClient(app) as client:
        response = client.post("/v1/documents", headers=AUTH, json=document_payload())
    assert response.status_code == 201
    with SessionLocal() as db:
        row = db.get(DocumentRow, response.json()["id"])
        assert row is not None
        assert row.object_key.endswith(".json")
        assert "Methods and participants" not in row.object_key


def test_canonical_document_analysis_completes_inline():
    with TestClient(app) as client:
        receipt = client.post("/v1/documents", headers=AUTH, json=document_payload())
        created = client.post(
            "/v1/analyses",
            headers=AUTH,
            json={"source": {"kind": "document", "value": receipt.json()["id"]}},
        )
        assert created.status_code == 202
        analysis_id = created.json()["id"]
        status = client.get(f"/v1/analyses/{analysis_id}", headers=AUTH)
        report = client.get(f"/v1/analyses/{analysis_id}/report", headers=AUTH)
    assert status.json()["state"] == "completed"
    assert status.json()["stage_started_at"]
    assert any(event["kind"] == "module" for event in status.json()["events"])
    assert report.status_code == 200
    assert report.json()["identity"]["title"] == "A canonical test paper"
    with SessionLocal() as db:
        row = db.get(AnalysisRow, analysis_id)
        assert row.source["kind"] == "document"
        assert "text" not in row.report


def resolved_sources() -> ResolvedPaper:
    return ResolvedPaper(
        id=uuid4(),
        identity=PaperIdentity(doi="10.1016/test", title="Fallback paper"),
        abstract="Fallback abstract.",
        content_level=ContentLevel.FULL_TEXT,
        candidates=[
            ContentCandidate(
                id="unpaywall-pdf",
                format=SourceFormat.PDF,
                url="https://publisher.example/paper.pdf",
                version="publishedVersion",
                provider="Unpaywall",
                content_level=ContentLevel.FULL_TEXT,
                rank=10,
            ),
            ContentCandidate(
                id="pmc-jats",
                format=SourceFormat.JATS,
                url="https://pmc.example/oai",
                version="publishedVersion",
                provider="PMC",
                content_level=ContentLevel.FULL_TEXT,
                rank=40,
            ),
        ],
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def store_resolution(resolution: ResolvedPaper) -> None:
    with SessionLocal() as db:
        ResolutionRepository(db).put(
            str(resolution.id),
            f"test-{resolution.id}",
            resolution.model_dump(mode="json"),
            900,
        )


def test_pdf_relay_error_is_safe_and_structured(monkeypatch):
    resolution = resolved_sources()
    store_resolution(resolution)

    async def fail_pdf(*args, **kwargs):
        raise ValueError("secret upstream URL https://publisher.example/paper.pdf returned 403")

    monkeypatch.setattr(routes_module, "fetch_bounded_pdf", fail_pdf)
    with TestClient(app) as client:
        response = client.get(
            f"/v1/resolutions/{resolution.id}/artifacts/unpaywall-pdf",
            headers=AUTH,
        )
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "source_unavailable",
        "candidate_id": "unpaywall-pdf",
        "message": "The selected PDF source could not be retrieved or validated.",
    }
    assert "publisher.example" not in response.text


def test_jats_document_persists_validated_fallback_provenance(monkeypatch):
    resolution = resolved_sources()
    store_resolution(resolution)
    text = "Methods\nParticipants were randomly assigned."

    async def jats_document(*args, **kwargs):
        return PaperDocument(
            identity=resolution.identity,
            content_level=ContentLevel.FULL_TEXT,
            source_format=SourceFormat.JATS,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            parser_name="pmc-jats",
            parser_version="test",
            text=text,
            spans=[{"id": "all", "text": text, "start": 0, "end": len(text)}],
        )

    monkeypatch.setattr(routes_module, "fetch_jats_document", jats_document)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/resolutions/{resolution.id}/documents/pmc-jats",
            headers=AUTH,
            json={"failed_candidate_ids": ["unpaywall-pdf"]},
        )
    assert response.status_code == 201
    with SessionLocal() as db:
        row = db.get(DocumentRow, response.json()["id"])
        document = get_document_store(get_settings()).get(row.object_key)
    assert document.extraction_warnings == [
        "PDF · Unpaywall · publishedVersion could not be used; analysis used "
        "JATS · PMC · publishedVersion instead."
    ]


def test_jats_document_rejects_foreign_fallback_candidate(monkeypatch):
    resolution = resolved_sources()
    store_resolution(resolution)
    text = "Full text"

    async def jats_document(*args, **kwargs):
        return PaperDocument(
            identity=resolution.identity,
            content_level=ContentLevel.FULL_TEXT,
            source_format=SourceFormat.JATS,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            parser_name="pmc-jats",
            parser_version="test",
            text=text,
        )

    monkeypatch.setattr(routes_module, "fetch_jats_document", jats_document)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/resolutions/{resolution.id}/documents/pmc-jats",
            headers=AUTH,
            json={"failed_candidate_ids": ["foreign-source"]},
        )
    assert response.status_code == 422
    assert "foreign-source" not in response.text
