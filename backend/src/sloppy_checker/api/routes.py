from __future__ import annotations

import hashlib
from datetime import UTC
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import Response as BinaryResponse
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings, get_settings
from sloppy_checker.core.database import (
    AnalysisRow,
    DocumentRow,
    get_db,
)
from sloppy_checker.core.dispatch import get_analysis_dispatcher
from sloppy_checker.core.repository import ResolutionRepository, SqlAlchemyAnalysisRepository
from sloppy_checker.core.schemas import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisState,
    AnalysisStatus,
    ContentCandidate,
    DocumentReceipt,
    PaperDocument,
    ResolvedDocumentPreparation,
    ResolvedPaper,
    ResolveRequest,
    SessionView,
)
from sloppy_checker.core.security import (
    AccessContext,
    issue_guest_session,
    require_client_access,
)
from sloppy_checker.core.storage import get_document_store
from sloppy_checker.evidence.resolver import PaperResolver, fetch_bounded_pdf, fetch_jats_document
from sloppy_checker.workflows.analysis import execute_analysis

router = APIRouter(prefix="/v1", dependencies=[Depends(require_client_access)])
RESOLUTION_CACHE_VERSION = "2"


def _status(row: AnalysisRow) -> AnalysisStatus:
    events = []
    for raw in row.events or []:
        event = dict(raw)
        legacy_stage = event.pop("stage", None)
        event.setdefault("kind", "stage")
        event.setdefault("label", legacy_stage or row.stage)
        event.setdefault("state", "completed" if event.get("progress") == 100 else "running")
        event.setdefault("evidence_count", 0)
        event.setdefault("notes", [])
        events.append(event)
    stage_events = [event for event in events if event["kind"] == "stage"]
    stage_started_at = stage_events[-1]["at"] if stage_events else row.updated_at
    return AnalysisStatus(
        id=UUID(row.id),
        state=AnalysisState(row.state),
        progress=row.progress,
        stage=row.stage,
        created_at=row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
        updated_at=row.updated_at.replace(tzinfo=row.updated_at.tzinfo or UTC),
        stage_started_at=stage_started_at,
        events=events,
        error=row.error,
    )


def _authorize_row(row: AnalysisRow, access: AccessContext) -> None:
    owner = (row.request or {}).get("_owner_hash")
    if not access.is_admin and (not owner or owner != access.owner_hash):
        raise HTTPException(404, "Analysis not found")


def _get_resolution(resolution_id: UUID, db: Session) -> ResolvedPaper:
    row = ResolutionRepository(db).get_by_id(str(resolution_id))
    if not row:
        raise HTTPException(404, "Resolution expired or was not found")
    return ResolvedPaper.model_validate(row.payload)


def _candidate(resolution: ResolvedPaper, candidate_id: str):
    candidate = next((item for item in resolution.candidates if item.id == candidate_id), None)
    if not candidate:
        raise HTTPException(404, "Resolved artifact was not found")
    return candidate


def _source_label(candidate: ContentCandidate) -> str:
    parts = [candidate.format.value.upper(), candidate.provider]
    if candidate.version:
        parts.append(candidate.version)
    return " · ".join(" ".join(part.split())[:80] for part in parts)


def _fallback_warnings(
    resolution: ResolvedPaper,
    failed_candidate_ids: list[str],
    used_candidate: ContentCandidate | None,
) -> list[str]:
    known = {candidate.id: candidate for candidate in resolution.candidates}
    unknown = [candidate_id for candidate_id in failed_candidate_ids if candidate_id not in known]
    if unknown:
        raise HTTPException(422, "A failed source does not belong to this resolution")
    used_label = _source_label(used_candidate) if used_candidate else (
        "abstract only" if resolution.abstract else "metadata only"
    )
    warnings = []
    for candidate_id in dict.fromkeys(failed_candidate_ids):
        failed = known[candidate_id]
        if used_candidate and failed.id == used_candidate.id:
            continue
        warnings.append(
            f"{_source_label(failed)} could not be used; analysis used {used_label} instead."
        )
    return warnings


@router.post("/session", response_model=SessionView)
def create_session(
    request: Request,
    response: Response,
    settings: AppSettings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> SessionView:
    value, owner_hash, expires_at = issue_guest_session(
        settings, request.cookies.get(settings.guest_cookie_name)
    )
    response.set_cookie(
        settings.guest_cookie_name,
        value,
        max_age=settings.report_retention_hours * 3600,
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        path="/",
    )
    return SessionView(
        expires_at=expires_at,
        hosted_remaining=None,
        concurrent_limit=settings.concurrent_runs_per_session,
    )


@router.post("/resolve", response_model=ResolvedPaper)
async def resolve_paper(
    payload: ResolveRequest,
    settings: AppSettings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ResolvedPaper:
    cache_input = f"{RESOLUTION_CACHE_VERSION}:{payload.value.strip().casefold()}"
    input_hash = hashlib.sha256(cache_input.encode()).hexdigest()
    repository = ResolutionRepository(db)
    cached = repository.get_by_input_hash(input_hash)
    if cached:
        return ResolvedPaper.model_validate(cached.payload)
    resolver = PaperResolver(settings)
    try:
        resolution = await resolver.resolve(payload.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await resolver.close()
    repository.put(
        str(resolution.id),
        input_hash,
        resolution.model_dump(mode="json"),
        settings.resolution_ttl_seconds,
    )
    return resolution


@router.get("/resolutions/{resolution_id}/artifacts/{candidate_id}")
async def relay_artifact(
    resolution_id: UUID,
    candidate_id: str,
    settings: AppSettings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> BinaryResponse:
    resolution = _get_resolution(resolution_id, db)
    candidate = _candidate(resolution, candidate_id)
    if candidate.format.value != "pdf" or not candidate.url:
        raise HTTPException(409, "This artifact is not a PDF")
    try:
        data = await fetch_bounded_pdf(str(candidate.url), settings)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502,
            "The selected PDF source could not be retrieved or validated.",
            headers={"X-SPC-Error-Code": "source_unavailable"},
        ) from exc
    return BinaryResponse(data, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


def _save_document(document: PaperDocument, owner_hash: str | None, db: Session, settings: AppSettings) -> DocumentReceipt:
    object_key = get_document_store(settings).put(document)
    row = DocumentRow(
        object_key=object_key,
        sha256=document.sha256,
        content_level=document.content_level.value,
        source_format=document.source_format.value,
        owner_hash=owner_hash,
    )
    db.add(row)
    db.commit()
    return DocumentReceipt(
        id=UUID(row.id),
        sha256=row.sha256,
        content_level=document.content_level,
        source_format=document.source_format,
    )


@router.post("/documents", response_model=DocumentReceipt, status_code=201)
def create_document(
    document: PaperDocument,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> DocumentReceipt:
    if len(document.text.encode()) > settings.max_upload_bytes * 2:
        raise HTTPException(413, "Parsed document is too large")
    return _save_document(document, access.owner_hash, db, settings)


@router.post(
    "/resolutions/{resolution_id}/documents/{candidate_id}",
    response_model=DocumentReceipt,
    status_code=201,
)
async def create_jats_document(
    resolution_id: UUID,
    candidate_id: str,
    payload: ResolvedDocumentPreparation | None = None,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> DocumentReceipt:
    resolution = _get_resolution(resolution_id, db)
    candidate = _candidate(resolution, candidate_id)
    if candidate.format.value != "jats":
        raise HTTPException(409, "This artifact is not JATS")
    try:
        document = await fetch_jats_document(candidate, resolution.identity, settings)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502,
            "The selected JATS source could not be retrieved or validated.",
            headers={"X-SPC-Error-Code": "source_unavailable"},
        ) from exc
    document.extraction_warnings.extend(
        _fallback_warnings(
            resolution,
            (payload or ResolvedDocumentPreparation()).failed_candidate_ids,
            candidate,
        )
    )
    return _save_document(document, access.owner_hash, db, settings)


def _enforce_quota(
    access: AccessContext,
    request: Request,
    mode: str,
    db: Session,
    settings: AppSettings,
) -> None:
    if access.is_admin or not access.owner_hash:
        return
    repository = SqlAlchemyAnalysisRepository(db)
    if repository.count_active(access.owner_hash) >= settings.concurrent_runs_per_session:
        raise HTTPException(429, "This anonymous session already has an active analysis")
    limit = settings.hosted_runs_per_session
    if limit is not None and repository.count_recent(access.owner_hash, mode) >= limit:
        raise HTTPException(429, f"Anonymous {mode} analysis quota reached")


@router.post("/analyses", response_model=AnalysisStatus, status_code=202)
async def create_analysis(
    payload: AnalysisRequest,
    request: Request,
    background: BackgroundTasks,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> AnalysisStatus:
    value = payload.source.value
    try:
        UUID(value)
        document = db.get(DocumentRow, value)
        if not document or (not access.is_admin and document.owner_hash != access.owner_hash):
            raise ValueError("Document not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    mode = "hosted"
    _enforce_quota(access, request, mode, db, settings)
    persisted_request = payload.model_dump(mode="json")
    persisted_request["_owner_hash"] = access.owner_hash
    persisted_request["provider_runtime"] = {
        "mode": mode,
        "profile": "token_factory",
    }
    source = {"kind": "document", "value": value}
    row = AnalysisRow(source=source, request=persisted_request, events=[])
    SqlAlchemyAnalysisRepository(db).add(row)
    try:
        dispatcher = get_analysis_dispatcher(settings, _run_background)
        row.task_id = await dispatcher.dispatch(row.id, background)
        db.commit()
    except Exception as exc:
        row.state = "failed"
        row.error = f"Dispatch failed: {type(exc).__name__}"
        row.stage = "Dispatch failed"
        db.commit()
        raise HTTPException(502, "Analysis could not be dispatched") from exc
    return _status(row)


async def _run_background(analysis_id: str, settings: AppSettings) -> None:
    from sloppy_checker.core.database import SessionLocal

    with SessionLocal() as session:
        await execute_analysis(analysis_id, session, settings)


@router.get("/analyses/{analysis_id}", response_model=AnalysisStatus)
def get_analysis(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
) -> AnalysisStatus:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    return _status(row)


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisStatus)
def cancel_analysis(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
) -> AnalysisStatus:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    if row.state not in {"completed", "failed", "cancelled"}:
        row.cancel_requested = True
        if row.state == "queued":
            row.state = "cancelled"
            row.stage = "Cancelled"
        db.commit()
    return _status(row)


@router.delete("/analyses/{analysis_id}", status_code=204)
def delete_analysis(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
) -> None:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    db.delete(row)
    db.commit()


@router.get("/analyses/{analysis_id}/report", response_model=AnalysisReport)
def get_report(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
) -> AnalysisReport:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    if row.state != "completed" or not row.report:
        raise HTTPException(409, "Report is not ready")
    return AnalysisReport.model_validate(row.report)
