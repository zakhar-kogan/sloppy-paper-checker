from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import Response as BinaryResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings, get_settings
from sloppy_checker.core.database import (
    AnalysisRow,
    DocumentRow,
    get_db,
)
from sloppy_checker.core.dispatch import get_analysis_dispatcher
from sloppy_checker.core.publication import publish_report
from sloppy_checker.core.repository import ResolutionRepository, SqlAlchemyAnalysisRepository
from sloppy_checker.core.reuse import (
    document_compatibility_hash,
    has_public_identifier,
    report_compatibility_hash,
)
from sloppy_checker.core.schemas import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisState,
    AnalysisStatus,
    ContentCandidate,
    DocumentReceipt,
    PaperDocument,
    PublicReportList,
    PublicReportSummary,
    PublishRequest,
    ResolvedDocumentPreparation,
    ResolvedPaper,
    ResolveRequest,
    ReusableAnalysis,
    SessionView,
)
from sloppy_checker.core.security import (
    AccessContext,
    issue_guest_session,
    require_client_access,
)
from sloppy_checker.core.storage import get_document_store
from sloppy_checker.evidence.resolver import PaperResolver, fetch_bounded_pdf, fetch_pmc_document
from sloppy_checker.workflows.analysis import classify_profile, execute_analysis

router = APIRouter(prefix="/v1", dependencies=[Depends(require_client_access)])
public_router = APIRouter(prefix="/v1/public")
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
def _public_summary(row: AnalysisRow) -> PublicReportSummary:
    if not row.public_slug or not row.published_at or not row.report:
        raise ValueError("Analysis is not published")
    report = AnalysisReport.model_validate(row.report)
    published = report.identity.published_at or ""
    year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    return PublicReportSummary(
        slug=row.public_slug,
        title=report.identity.title or "Untitled paper",
        year=year,
        profile=report.profile,
        content_level=report.content_level,
        review_score=report.review_score,
        coverage=report.coverage.full_review,
        provisional=report.coverage.provisional,
        concern_count=sum(
            finding.grade.value in {"critical_concern", "major_concern", "minor_concern"}
            for finding in report.findings
        ),
        published_at=row.published_at.replace(tzinfo=row.published_at.tzinfo or UTC),
        expires_at=row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC),
    )




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
        max_age=settings.guest_session_lifetime_seconds,
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        path="/",
    )
    repository = SqlAlchemyAnalysisRepository(db)
    session_limit = settings.hosted_runs_per_session
    return SessionView(
        expires_at=expires_at,
        hosted_remaining=(
            None
            if session_limit is None
            else max(0, session_limit - repository.count_recent(owner_hash, "hosted"))
        ),
        concurrent_limit=settings.concurrent_runs_per_session,
        live_analysis_enabled=settings.live_analysis_enabled,
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
async def create_pmc_document(
    resolution_id: UUID,
    candidate_id: str,
    payload: ResolvedDocumentPreparation | None = None,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> DocumentReceipt:
    resolution = _get_resolution(resolution_id, db)
    candidate = _candidate(resolution, candidate_id)
    if candidate.provider != "PMC" or candidate.format.value not in {"jats", "html"}:
        raise HTTPException(409, "This artifact is not PMC full text")
    try:
        document = await fetch_pmc_document(candidate, resolution.identity, settings)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502,
            "The selected PMC full-text source could not be retrieved or validated.",
            headers={"X-SPC-Error-Code": "source_unavailable"},
        ) from exc
    document.source_url = candidate.url
    document.source_provider = candidate.provider
    document.source_version = candidate.version
    document.extraction_warnings.extend(
        _fallback_warnings(
            resolution,
            (payload or ResolvedDocumentPreparation()).failed_candidate_ids,
            candidate,
        )
    )
    return _save_document(document, access.owner_hash, db, settings)

@router.get(
    "/documents/{document_id}/reusable-analysis",
    response_model=ReusableAnalysis | None,
)
def get_reusable_analysis(
    document_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> ReusableAnalysis | None:
    document_row = db.get(DocumentRow, str(document_id))
    if not document_row or (not access.is_admin and document_row.owner_hash != access.owner_hash):
        raise HTTPException(404, "Document not found")
    document = get_document_store(settings).get(document_row.object_key)
    compatibility_hash = document_compatibility_hash(
        document,
        classify_profile(document.text),
        settings,
    )
    now = datetime.now(UTC)
    rows = list(
        db.scalars(
            select(AnalysisRow)
            .where(
                AnalysisRow.state == "completed",
                AnalysisRow.expires_at > now,
                AnalysisRow.report.is_not(None),
            )
            .order_by(AnalysisRow.updated_at.desc())
        )
    )
    owned: AnalysisRow | None = None
    public: AnalysisRow | None = None
    for row in rows:
        report = AnalysisReport.model_validate(row.report)
        if report_compatibility_hash(report) != compatibility_hash:
            continue
        if (row.request or {}).get("_owner_hash") == access.owner_hash:
            owned = row
            break
        if (
            public is None
            and row.published_at is not None
            and row.public_slug is not None
            and has_public_identifier(report)
        ):
            public = row
    match = owned or public
    if not match or not match.report:
        return None
    report = AnalysisReport.model_validate(match.report)
    return ReusableAnalysis(
        access="owned" if match is owned else "public",
        analysis_id=UUID(match.id) if match is owned else None,
        slug=match.public_slug if match is public else None,
        title=report.identity.title or report.identity.doi or "Untitled paper",
        completed_at=report.completed_at,
        profile=report.profile,
        content_level=report.content_level,
        source_format=report.source_format,
        review_score=report.review_score,
        coverage=report.coverage.full_review,
        methodology_version=report.methodology_version,
        worker_model=report.worker_model,
        reviewer_model=report.reviewer_model,
    )




def _enforce_quota(
    access: AccessContext,
    request: Request,
    mode: str,
    db: Session,
    settings: AppSettings,
) -> None:
    if access.is_admin:
        return
    if not settings.live_analysis_enabled:
        raise HTTPException(
            503,
            "Live analysis is temporarily paused",
            headers={"Retry-After": "3600", "X-SPC-Error-Code": "analysis_paused"},
        )
    if not access.owner_hash:
        raise HTTPException(401, "A guest session is required")
    repository = SqlAlchemyAnalysisRepository(db)
    if repository.count_active(access.owner_hash) >= settings.concurrent_runs_per_session:
        raise HTTPException(
            429,
            "This anonymous session already has an active analysis",
            headers={"Retry-After": "60"},
        )
    limit = settings.hosted_runs_per_session
    if limit is not None and repository.count_recent(access.owner_hash, mode) >= limit:
        raise HTTPException(
            429,
            f"Anonymous {mode} analysis quota reached",
            headers={"Retry-After": "86400"},
        )


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
        "profile": settings.provider_profile,
    }
    source = {"kind": "document", "value": value}
    row = AnalysisRow(
        source=source,
        request=persisted_request,
        owner_hash=access.owner_hash,
        provider_mode=mode,
        events=[],
        expires_at=datetime.now(UTC) + timedelta(hours=settings.report_retention_hours),
    )
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
@router.get(
    "/analyses/{analysis_id}/publication",
    response_model=PublicReportSummary | None,
)
def get_publication(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
) -> PublicReportSummary | None:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    if not row.public_slug or not row.published_at:
        return None
    return _public_summary(row)




@router.post(
    "/analyses/{analysis_id}/publish",
    response_model=PublicReportSummary,
)
def publish_analysis(
    analysis_id: UUID,
    payload: PublishRequest,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> PublicReportSummary:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    if row.state != "completed" or not row.report:
        raise HTTPException(409, "Only completed reports can be published")
    if not payload.confirm_public:
        raise HTTPException(422, "Public sharing must be explicitly confirmed")
    try:
        publish_report(row, db, settings)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    db.commit()
    db.refresh(row)
    return _public_summary(row)


@router.delete("/analyses/{analysis_id}/publish", status_code=204)
def unpublish_analysis(
    analysis_id: UUID,
    access: AccessContext = Depends(require_client_access),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> None:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    _authorize_row(row, access)
    row.public_slug = None
    row.published_at = None
    row.expires_at = datetime.now(UTC) + timedelta(hours=settings.report_retention_hours)
    db.commit()


@public_router.get("/reports", response_model=PublicReportList)
def list_public_reports(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> PublicReportList:
    rows = SqlAlchemyAnalysisRepository(db).list_public(limit)
    return PublicReportList(reports=[_public_summary(row) for row in rows])


@public_router.get("/reports/{slug}", response_model=AnalysisReport)
def get_public_report(slug: str, db: Session = Depends(get_db)) -> AnalysisReport:
    row = SqlAlchemyAnalysisRepository(db).get_public(slug)
    if not row or not row.report:
        raise HTTPException(404, "Public report not found")
    return AnalysisReport.model_validate(row.report)
