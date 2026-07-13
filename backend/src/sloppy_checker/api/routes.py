from __future__ import annotations

import asyncio
import json
from datetime import UTC
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings, get_settings
from sloppy_checker.core.database import (
    AnalysisRow,
    ProviderSettingsRow,
    UploadRow,
    get_db,
)
from sloppy_checker.core.ingest import normalize_doi, save_pdf
from sloppy_checker.core.schemas import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisState,
    AnalysisStatus,
    PaperSourceKind,
    ProviderSettingsInput,
    ProviderSettingsView,
    UploadReceipt,
)
from sloppy_checker.core.security import (
    decrypt_secret,
    encrypt_secret,
    require_api_token,
    validate_public_url,
)
from sloppy_checker.tasks import analyze_paper
from sloppy_checker.workflows.analysis import execute_analysis

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


def _status(row: AnalysisRow) -> AnalysisStatus:
    return AnalysisStatus(
        id=UUID(row.id),
        state=AnalysisState(row.state),
        progress=row.progress,
        stage=row.stage,
        created_at=row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
        updated_at=row.updated_at.replace(tzinfo=row.updated_at.tzinfo or UTC),
        error=row.error,
    )


@router.post("/uploads", response_model=UploadReceipt, status_code=201)
async def upload_pdf(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> UploadReceipt:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(415, "Only PDF uploads are accepted")
    try:
        digest, size, path = await save_pdf(file, settings)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    row = UploadRow(path=str(path), sha256=digest, size=size)
    db.add(row)
    db.commit()
    return UploadReceipt(id=UUID(row.id), sha256=digest, bytes=size)


@router.post("/analyses", response_model=AnalysisStatus, status_code=202)
async def create_analysis(
    payload: AnalysisRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> AnalysisStatus:
    value = payload.source.value
    try:
        if payload.source.kind == PaperSourceKind.DOI:
            value = normalize_doi(value)
        elif payload.source.kind == PaperSourceKind.URL:
            value = validate_public_url(value)
        else:
            UUID(value)
            if not db.get(UploadRow, value):
                raise ValueError("Upload not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    source = {"kind": payload.source.kind.value, "value": value}
    row = AnalysisRow(source=source, request=payload.model_dump(mode="json"), events=[])
    db.add(row)
    db.commit()
    if settings.eager_tasks:
        background.add_task(_run_background, row.id, settings)
    else:
        task = analyze_paper.delay(row.id)
        row.task_id = task.id
        db.commit()
    return _status(row)


async def _run_background(analysis_id: str, settings: AppSettings) -> None:
    from sloppy_checker.core.database import SessionLocal

    with SessionLocal() as session:
        await execute_analysis(analysis_id, session, settings)


@router.get("/analyses/{analysis_id}", response_model=AnalysisStatus)
def get_analysis(analysis_id: UUID, db: Session = Depends(get_db)) -> AnalysisStatus:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    return _status(row)


@router.get("/analyses/{analysis_id}/events")
async def analysis_events(
    analysis_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    if not db.get(AnalysisRow, str(analysis_id)):
        raise HTTPException(404, "Analysis not found")

    async def stream():
        sent = 0
        terminal = {"completed", "failed", "cancelled"}
        while not await request.is_disconnected():
            db.expire_all()
            row = db.get(AnalysisRow, str(analysis_id))
            if not row:
                break
            events = row.events or []
            for event in events[sent:]:
                yield f"event: progress\ndata: {json.dumps(event)}\n\n"
            sent = len(events)
            if row.state in terminal:
                yield f"event: terminal\ndata: {json.dumps({'state': row.state})}\n\n"
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisStatus)
def cancel_analysis(analysis_id: UUID, db: Session = Depends(get_db)) -> AnalysisStatus:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    if row.state not in {"completed", "failed", "cancelled"}:
        row.cancel_requested = True
        if row.state == "queued":
            row.state = "cancelled"
            row.stage = "Cancelled"
        db.commit()
    return _status(row)


@router.get("/analyses/{analysis_id}/report", response_model=AnalysisReport)
def get_report(analysis_id: UUID, db: Session = Depends(get_db)) -> AnalysisReport:
    row = db.get(AnalysisRow, str(analysis_id))
    if not row:
        raise HTTPException(404, "Analysis not found")
    if row.state != "completed" or not row.report:
        raise HTTPException(409, "Report is not ready")
    return AnalysisReport.model_validate(row.report)


def _provider_view(row: ProviderSettingsRow | None, settings: AppSettings) -> ProviderSettingsView:
    values = (row.values if row else {}) or {}
    if settings.nebius_api_key:
        source = "environment"
    elif row and row.encrypted_api_key:
        source = "encrypted"
    else:
        source = "missing"
    return ProviderSettingsView(
        base_url=values.get("base_url", "https://api.tokenfactory.nebius.com/v1/"),
        has_api_key=source != "missing",
        api_key_source=source,
        planner_model=values.get("planner_model", ""),
        worker_model=values.get("worker_model", ""),
        critic_model=values.get("critic_model", ""),
        default_depth=values.get("default_depth", "standard"),
        max_concurrency=values.get("max_concurrency", 5),
        sequential_mode=values.get("sequential_mode", False),
        max_cost_usd=values.get("max_cost_usd", 2.0),
        retention_days=values.get("retention_days", 30),
    )


@router.get("/settings", response_model=ProviderSettingsView)
def get_provider_settings(
    db: Session = Depends(get_db), settings: AppSettings = Depends(get_settings)
) -> ProviderSettingsView:
    return _provider_view(db.get(ProviderSettingsRow, 1), settings)


@router.put("/settings", response_model=ProviderSettingsView)
def put_provider_settings(
    payload: ProviderSettingsInput,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_settings),
) -> ProviderSettingsView:
    row = db.get(ProviderSettingsRow, 1) or ProviderSettingsRow(id=1, values={})
    values = payload.model_dump(mode="json", exclude={"api_key", "clear_api_key"})
    row.values = values
    if payload.clear_api_key:
        row.encrypted_api_key = None
    elif payload.api_key:
        row.encrypted_api_key = encrypt_secret(payload.api_key, settings)
    db.add(row)
    db.commit()
    return _provider_view(row, settings)


@router.get("/providers/models")
async def provider_models(
    db: Session = Depends(get_db), settings: AppSettings = Depends(get_settings)
) -> dict[str, list[dict[str, str]]]:
    row = db.get(ProviderSettingsRow, 1)
    values = (row.values if row else {}) or {}
    key = settings.nebius_api_key
    if not key and row and row.encrypted_api_key:
        key = decrypt_secret(row.encrypted_api_key, settings)
    if not key:
        raise HTTPException(409, "Configure a provider API key first")
    base = str(values.get("base_url", "https://api.tokenfactory.nebius.com/v1/")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(base + "/models", headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Provider model discovery failed") from exc
    models = [
        {"id": str(item.get("id", "")), "owned_by": str(item.get("owned_by", ""))}
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {"models": models}

