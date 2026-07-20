from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppSettings
from .database import AnalysisRow, DocumentRow, ResolutionRow
from .storage import get_document_store


@dataclass(frozen=True)
class CleanupResult:
    analyses: int = 0
    documents: int = 0
    resolutions: int = 0
    document_errors: int = 0


def cleanup_expired(db: Session, settings: AppSettings) -> CleanupResult:
    """Delete expired terminal analyses and unreferenced stored documents."""
    now = datetime.now(UTC)
    expired = list(
        db.scalars(
            select(AnalysisRow).where(
                AnalysisRow.expires_at <= now,
                AnalysisRow.state.in_(("completed", "failed", "cancelled")),
            )
        )
    )
    expired_ids = {row.id for row in expired}
    remaining_document_ids = {
        str((row.source or {}).get("value"))
        for row in db.scalars(select(AnalysisRow))
        if row.id not in expired_ids and (row.source or {}).get("kind") == "document"
    }

    for row in expired:
        db.delete(row)

    expired_resolutions = list(
        db.scalars(select(ResolutionRow).where(ResolutionRow.expires_at <= now))
    )
    for row in expired_resolutions:
        db.delete(row)

    document_errors = 0
    deleted_documents = 0
    cutoff = now - timedelta(hours=settings.report_retention_hours)
    orphaned_documents = list(
        db.scalars(select(DocumentRow).where(DocumentRow.created_at <= cutoff))
    )
    store = get_document_store(settings)
    for document in orphaned_documents:
        if document.id in remaining_document_ids:
            continue
        try:
            store.delete(document.object_key)
        except Exception:
            document_errors += 1
            continue
        db.delete(document)
        deleted_documents += 1

    db.commit()
    return CleanupResult(
        analyses=len(expired),
        documents=deleted_documents,
        resolutions=len(expired_resolutions),
        document_errors=document_errors,
    )
