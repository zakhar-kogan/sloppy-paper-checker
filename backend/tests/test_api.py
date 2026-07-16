import hashlib

from fastapi.testclient import TestClient

from sloppy_checker.core.database import AnalysisRow, DocumentRow, SessionLocal
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
    assert report.status_code == 200
    assert report.json()["identity"]["title"] == "A canonical test paper"
    with SessionLocal() as db:
        row = db.get(AnalysisRow, analysis_id)
        assert row.source["kind"] == "document"
        assert "text" not in row.report
