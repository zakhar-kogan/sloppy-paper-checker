from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import AnalysisRow, ResolutionRow


class AnalysisRepository(Protocol):
    def add(self, row: AnalysisRow) -> AnalysisRow: ...

    def get(self, analysis_id: str) -> AnalysisRow | None: ...

    def save(self, row: AnalysisRow) -> None: ...

    def delete(self, row: AnalysisRow) -> None: ...

    def count_recent(self, owner_hash: str, mode: str, hours: int = 24) -> int: ...
    def count_recent_global(self, mode: str, hours: int = 24) -> int: ...

    def get_public(self, slug: str) -> AnalysisRow | None: ...

    def list_public(self, limit: int = 20) -> list[AnalysisRow]: ...

    def count_active(self, owner_hash: str) -> int: ...


class SqlAlchemyAnalysisRepository:
    """Narrow persistence boundary shared by SQLite and PostgreSQL."""

    def __init__(self, session: Session):
        self.session = session

    def add(self, row: AnalysisRow) -> AnalysisRow:
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(self, analysis_id: str) -> AnalysisRow | None:
        return self.session.get(AnalysisRow, analysis_id)

    def save(self, row: AnalysisRow) -> None:
        self.session.add(row)
        self.session.commit()

    def delete(self, row: AnalysisRow) -> None:
        self.session.delete(row)
        self.session.commit()

    def count_recent(self, owner_hash: str, mode: str, hours: int = 24) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        rows = self.session.scalars(
            select(AnalysisRow).where(AnalysisRow.created_at >= cutoff)
        )
        return sum(
            (row.request or {}).get("_owner_hash") == owner_hash
            and ((row.request or {}).get("provider_runtime") or {}).get("mode", "hosted") == mode
            for row in rows
        )

    def count_recent_global(self, mode: str, hours: int = 24) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        rows = self.session.scalars(
            select(AnalysisRow).where(AnalysisRow.created_at >= cutoff)
        )
        return sum(
            bool((row.request or {}).get("_owner_hash"))
            and ((row.request or {}).get("provider_runtime") or {}).get("mode", "hosted") == mode
            for row in rows
        )



    def get_public(self, slug: str) -> AnalysisRow | None:
        return self.session.scalar(
            select(AnalysisRow).where(
                AnalysisRow.public_slug == slug,
                AnalysisRow.published_at.is_not(None),
                AnalysisRow.expires_at > datetime.now(UTC),
                AnalysisRow.state == "completed",
            )
        )

    def list_public(self, limit: int = 20) -> list[AnalysisRow]:
        return list(
            self.session.scalars(
                select(AnalysisRow)
                .where(
                    AnalysisRow.published_at.is_not(None),
                    AnalysisRow.expires_at > datetime.now(UTC),
                    AnalysisRow.state == "completed",
                )
                .order_by(AnalysisRow.published_at.desc())
                .limit(limit)
            )
        )

    def count_active(self, owner_hash: str) -> int:
        rows = self.session.scalars(
            select(AnalysisRow).where(AnalysisRow.state.in_(("queued", "running")))
        )
        return sum((row.request or {}).get("_owner_hash") == owner_hash for row in rows)


class ResolutionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, resolution_id: str) -> ResolutionRow | None:
        row = self.session.get(ResolutionRow, resolution_id)
        return row if row and self._fresh(row) else None

    def get_by_input_hash(self, input_hash: str) -> ResolutionRow | None:
        row = self.session.scalar(
            select(ResolutionRow).where(ResolutionRow.input_hash == input_hash)
        )
        return row if row and self._fresh(row) else None

    def put(self, resolution_id: str, input_hash: str, payload: dict, ttl_seconds: int) -> None:
        old = self.session.scalar(
            select(ResolutionRow).where(ResolutionRow.input_hash == input_hash)
        )
        if old:
            self.session.delete(old)
            self.session.flush()
        self.session.add(
            ResolutionRow(
                id=resolution_id,
                input_hash=input_hash,
                payload=payload,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            )
        )
        self.session.commit()

    @staticmethod
    def _fresh(row: ResolutionRow) -> bool:
        expires = row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC)
        return expires > datetime.now(UTC)
