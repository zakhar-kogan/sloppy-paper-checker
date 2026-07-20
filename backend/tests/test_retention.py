from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sloppy_checker.core.config import get_settings
from sloppy_checker.core.database import (
    AnalysisRow,
    DocumentRow,
    ResolutionRow,
    SessionLocal,
    create_schema,
)
from sloppy_checker.core.retention import cleanup_expired
from sloppy_checker.core.schemas import ContentLevel, PaperDocument, PaperIdentity, SourceFormat
from sloppy_checker.core.storage import get_document_store


def _stored_document(title: str) -> tuple[DocumentRow, str]:
    settings = get_settings()
    document = PaperDocument(
        identity=PaperIdentity(title=title),
        content_level=ContentLevel.METADATA,
        source_format=SourceFormat.METADATA,
        sha256=(title.encode().hex() + "0" * 64)[:64],
        parser_name="test",
        parser_version="1",
        text=title,
    )
    object_key = get_document_store(settings).put(document)
    return (
        DocumentRow(
            object_key=object_key,
            sha256=document.sha256,
            content_level=document.content_level.value,
            source_format=document.source_format.value,
            created_at=datetime.now(UTC) - timedelta(hours=48),
        ),
        object_key,
    )


def test_cleanup_removes_expired_private_and_public_data_but_retains_active_reports():
    create_schema()
    settings = get_settings()
    private_document, private_key = _stored_document("Expired private")
    public_document, public_key = _stored_document("Published report")
    active_document, active_key = _stored_document("Active report")
    expired_at = datetime.now(UTC) - timedelta(hours=1)

    with SessionLocal() as db:
        db.add_all([private_document, public_document, active_document])
        db.flush()
        private = AnalysisRow(
            state="completed",
            source={"kind": "document", "value": private_document.id},
            request={},
            expires_at=expired_at,
        )
        public = AnalysisRow(
            state="failed",
            source={"kind": "document", "value": public_document.id},
            request={},
            report={},
            expires_at=expired_at,
            published_at=datetime.now(UTC),
            public_slug="published-test-report",
        )
        active = AnalysisRow(
            state="running",
            source={"kind": "document", "value": active_document.id},
            request={},
            expires_at=expired_at,
        )
        resolution = ResolutionRow(
            id=str(uuid4()),
            input_hash="f" * 64,
            payload={},
            expires_at=expired_at,
        )
        db.add_all([private, public, active, resolution])
        db.commit()
        private_id = private.id
        public_id = public.id
        active_id = active.id
        resolution_id = resolution.id

        result = cleanup_expired(db, settings)
        assert result.analyses == 2
        assert result.documents == 2
        assert result.resolutions == 1
        assert result.document_errors == 0
        assert db.get(AnalysisRow, private_id) is None
        assert db.get(AnalysisRow, public_id) is None
        assert db.get(AnalysisRow, active_id) is not None
        assert db.get(ResolutionRow, resolution_id) is None
        assert db.get(DocumentRow, private_document.id) is None
        assert db.get(DocumentRow, public_document.id) is None
        assert db.get(DocumentRow, active_document.id) is not None

    store = get_document_store(settings)
    with pytest.raises(FileNotFoundError):
        store.get(private_key)
    with pytest.raises(FileNotFoundError):
        store.get(public_key)
    assert store.get(active_key).identity.title == "Active report"
