import respx
from fastapi.testclient import TestClient
from httpx import Response

from sloppy_checker.core.database import ProviderSettingsRow, SessionLocal
from sloppy_checker.main import app

AUTH = {"Authorization": "Bearer development-only-change-me"}


def test_api_requires_bearer_token():
    with TestClient(app) as client:
        assert client.get("/v1/settings").status_code == 401
        assert client.get("/healthz").status_code == 200


def test_settings_encrypt_and_redact_provider_key():
    payload = {
        "base_url": "https://api.tokenfactory.nebius.com/v1/",
        "api_key": "secret-test-key",
        "planner_model": "planner",
        "worker_model": "worker",
        "critic_model": "critic",
        "default_depth": "standard",
        "max_concurrency": 4,
        "sequential_mode": False,
        "max_cost_usd": 2,
        "retention_days": 7,
    }
    with TestClient(app) as client:
        response = client.put("/v1/settings", headers=AUTH, json=payload)
        assert response.status_code == 200
        assert response.json()["has_api_key"] is True
        assert "api_key" not in response.json()
        fetched = client.get("/v1/settings", headers=AUTH)
        assert fetched.json()["api_key_source"] == "encrypted"


def test_upload_rejects_non_pdf_content():
    with TestClient(app) as client:
        response = client.post(
            "/v1/uploads",
            headers=AUTH,
            files={"file": ("not-a-paper.pdf", b"not a pdf", "application/pdf")},
        )
        assert response.status_code == 400


@respx.mock
def test_doi_analysis_runs_end_to_end_and_persists_only_report():
    with SessionLocal() as db:
        row = db.get(ProviderSettingsRow, 1)
        if row:
            row.values = {}
            row.encrypted_api_key = None
            db.commit()
    crossref = respx.get("https://api.crossref.org/works/10.5555/test.1").mock(
        return_value=Response(
            200,
            json={
                "message": {
                    "title": ["A traceable test paper"],
                    "abstract": "Methods and participants. Statistical analysis used confidence intervals. Results and discussion. Data availability: repository.",
                    "author": [{"given": "Ada", "family": "Example"}],
                    "container-title": ["Journal of Test Fixtures"],
                }
            },
        )
    )
    respx.get("https://api.openalex.org/works/https://doi.org/10.5555/test.1").mock(
        return_value=Response(200, json={"id": "https://openalex.org/W1"})
    )
    with TestClient(app) as client:
        created = client.post(
            "/v1/analyses",
            headers=AUTH,
            json={"source": {"kind": "doi", "value": "10.5555/test.1"}},
        )
        assert created.status_code == 202
        analysis_id = created.json()["id"]
        status = client.get(f"/v1/analyses/{analysis_id}", headers=AUTH)
        assert status.json()["state"] == "completed"
        report = client.get(f"/v1/analyses/{analysis_id}/report", headers=AUTH)
        assert report.status_code == 200
        assert report.json()["identity"]["title"] == "A traceable test paper"
        assert "full_text" not in report.json()
    assert crossref.call_count == 2
