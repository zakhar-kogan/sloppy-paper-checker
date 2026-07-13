from datetime import UTC, datetime, timedelta

from sloppy_checker.core.database import AnalysisRow, SessionLocal, UploadRow
from sloppy_checker.tasks import purge_expired


def test_retention_removes_expired_reports_and_upload_bytes(tmp_path):
    source = tmp_path / "expired.pdf"
    source.write_bytes(b"%PDF-expired")
    old = datetime.now(UTC) - timedelta(days=31)
    with SessionLocal() as db:
        analysis = AnalysisRow(
            state="completed",
            progress=100,
            stage="Complete",
            source={"kind": "doi", "value": "10.5555/expired"},
            request={},
            report={"schema_version": "1.0"},
            events=[],
            created_at=old,
            updated_at=old,
        )
        upload = UploadRow(path=str(source), sha256="0" * 64, size=12, created_at=old)
        db.add_all([analysis, upload])
        db.commit()
        analysis_id, upload_id = analysis.id, upload.id

    result = purge_expired.run()

    with SessionLocal() as db:
        assert db.get(AnalysisRow, analysis_id) is None
        assert db.get(UploadRow, upload_id) is None
    assert not source.exists()
    assert result["reports"] >= 1
    assert result["uploads"] >= 1

