import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from celery import Celery

from sloppy_checker.core.config import get_settings
from sloppy_checker.core.database import AnalysisRow, ProviderSettingsRow, SessionLocal, UploadRow
from sloppy_checker.workflows.analysis import execute_analysis

settings = get_settings()
celery_app = Celery("sloppy_checker", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.eager_tasks,
)
celery_app.conf.beat_schedule = {
    "purge-expired-analysis-material": {
        "task": "sloppy_checker.tasks.purge_expired",
        "schedule": 86400.0,
    }
}


@celery_app.task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def analyze_paper(self, analysis_id: str) -> None:
    with SessionLocal() as db:
        asyncio.run(execute_analysis(analysis_id, db, settings))


@celery_app.task(name="sloppy_checker.tasks.purge_expired")
def purge_expired() -> dict[str, int]:
    removed_reports = 0
    removed_uploads = 0
    with SessionLocal() as db:
        provider = db.get(ProviderSettingsRow, 1)
        days = int(((provider.values if provider else {}) or {}).get("retention_days", 30))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        for upload in db.query(UploadRow).filter(UploadRow.created_at < cutoff).all():
            Path(upload.path).unlink(missing_ok=True)
            db.delete(upload)
            removed_uploads += 1
        if days == 0:
            candidates = db.query(AnalysisRow).filter(AnalysisRow.state.in_(["completed", "failed", "cancelled"]))
        else:
            candidates = db.query(AnalysisRow).filter(AnalysisRow.updated_at < cutoff)
        for analysis in candidates.all():
            db.delete(analysis)
            removed_reports += 1
        db.commit()
    return {"reports": removed_reports, "uploads": removed_uploads}
