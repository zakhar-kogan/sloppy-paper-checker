from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppSettings
from .database import AnalysisRow


def publish_report(row: AnalysisRow, db: Session, settings: AppSettings) -> None:
    """Publish a completed report without extending an existing publication."""
    if not row.public_slug:
        for _ in range(5):
            slug = secrets.token_urlsafe(12)
            if db.scalar(select(AnalysisRow.id).where(AnalysisRow.public_slug == slug)) is None:
                row.public_slug = slug
                break
        else:
            raise RuntimeError("A public report link could not be created")
    if row.published_at is None:
        row.published_at = datetime.now(UTC)
        row.expires_at = row.published_at + timedelta(days=settings.public_report_retention_days)
