from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.database import AnalysisRow, DocumentRow
from sloppy_checker.core.ingest import fingerprint_text
from sloppy_checker.core.methodology import content_allows, load_methodology
from sloppy_checker.core.rubrics import rubric_prompt
from sloppy_checker.core.schemas import (
    AnalysisEvidenceNote,
    AnalysisReport,
    ConfidenceComponents,
    ContentLevel,
    ContextAssessment,
    EvidenceSource,
    Finding,
    FindingSeverity,
    PaperIdentity,
    RubricGrade,
    RubricProfile,
    SourceFormat,
)
from sloppy_checker.core.scoring import score_findings
from sloppy_checker.core.security import validate_public_url
from sloppy_checker.core.storage import get_document_store
from sloppy_checker.evidence.adapters import EvidenceClient
from sloppy_checker.workflows.routing import chunk_document, format_routed_chunks, route_chunks


class EvidenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_item: str
    observation: str
    quotes: list[str] = Field(default_factory=list)


class WorkerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str
    items: list[EvidenceNote] = Field(default_factory=list)
    raw_text: str = ""


class WorkerNoteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_item: str
    observation: str
    quotes: list[str] = Field(default_factory=list, max_length=2)


class WorkerEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[WorkerNoteOutput]


class FinalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_item: str
    grade: RubricGrade
    explanation: str
    confidence: float = Field(ge=0, le=1)
    evidence_quotes: list[str] = Field(default_factory=list)


class FinalAssessmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[FinalDecision]
    summary: list[str] = Field(max_length=6)


@dataclass(frozen=True)
class ParsedAssessment:
    findings: list[Finding]
    summary: list[str]
    missing_item_ids: list[str]
    assessed_attempts: int
    grounded_assessed: int
    validation_warnings: list[str]


@dataclass(frozen=True)
class AdjudicationResult:
    findings: list[Finding]
    summary: list[str]
    missing_item_ids: list[str]
    repaired_output: bool
    assessed_attempts: int
    grounded_assessed: int
    validation_warnings: list[str]
    usage: dict[str, int]


class AnalysisCancelled(Exception):
    """Raised when a durable cancellation request interrupts an active model call."""


def _json_value(content: object) -> object:
    if isinstance(content, BaseModel):
        return content.model_dump(mode="json")
    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
        return json.loads(cleaned)
    return content


def _content_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    try:
        value = _json_value(content)
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content).strip()


def _canonical_grade(value: object) -> RubricGrade | None:
    normalized = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    aliases = {
        "none": RubricGrade.NO_CONCERN,
        "no_issue": RubricGrade.NO_CONCERN,
        "no_concern": RubricGrade.NO_CONCERN,
        "minor": RubricGrade.MINOR_CONCERN,
        "minor_concern": RubricGrade.MINOR_CONCERN,
        "major": RubricGrade.MAJOR_CONCERN,
        "major_concern": RubricGrade.MAJOR_CONCERN,
        "critical": RubricGrade.CRITICAL_CONCERN,
        "critical_concern": RubricGrade.CRITICAL_CONCERN,
        "not_assessed": RubricGrade.NOT_ASSESSED,
        "insufficient_evidence": RubricGrade.NOT_ASSESSED,
        "unknown": RubricGrade.NOT_ASSESSED,
    }
    return aliases.get(normalized)


def _candidate_quotes(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        quote = value.get("quote") or value.get("text") or value.get("evidence")
        return [str(quote)] if quote else []
    if isinstance(value, list):
        quotes: list[str] = []
        for item in value:
            quotes.extend(_candidate_quotes(item))
        return quotes
    return []


def _ground_quote(value: str, paper_text: str) -> str | None:
    quote = value.strip().strip('"\'“”‘’').strip()
    if not quote:
        return None
    if quote in paper_text:
        return quote
    tokens = quote.split()
    if not tokens or len(quote) > 1200:
        return None
    match = re.search(r"\s+".join(re.escape(token) for token in tokens), paper_text)
    return match.group(0) if match else None


def _coerce_worker_evidence(
    content: object,
    module_key: str,
    expected_items: list[str],
    paper_text: str,
) -> WorkerEvidence:
    """Extract useful worker notes when possible and always preserve non-empty raw output."""
    raw_text = _content_text(content)
    try:
        payload = _json_value(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return WorkerEvidence(module=module_key, raw_text=raw_text)
    records: list[object] = []
    if isinstance(payload, list):
        records = list(payload)
    elif isinstance(payload, dict):
        for key in ("evidence", "items", "notes", "findings", "assessments", "results"):
            if isinstance(payload.get(key), list):
                records = list(payload[key])
                break
        if not records and any(key in payload for key in ("rubric_item", "item", "criterion")):
            records = [payload]
    notes: list[EvidenceNote] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        rubric_item = record.get("rubric_item") or record.get("item") or record.get("criterion")
        if rubric_item not in expected_items:
            continue
        quotes = _candidate_quotes(
            record.get("quotes")
            or record.get("evidence_quotes")
            or record.get("supporting_quotes")
            or record.get("paper_spans")
        )
        if not quotes:
            quotes = _candidate_quotes(
                record.get("evidence") or record.get("quote") or record.get("citation")
            )
        grounded_quotes = [grounded for quote in quotes if (grounded := _ground_quote(quote, paper_text))]
        observation = (
            record.get("observation")
            or record.get("notes")
            or record.get("explanation")
            or record.get("reasoning")
            or record.get("rationale")
            or record.get("finding")
            or "Relevant evidence was identified without a separate explanation."
        )
        notes.append(
            EvidenceNote(
                rubric_item=str(rubric_item),
                observation=str(observation),
                quotes=list(dict.fromkeys(grounded_quotes)),
            )
        )
    return WorkerEvidence(module=module_key, items=notes, raw_text=raw_text)


def _analysis_notes(evidence: list[WorkerEvidence]) -> list[AnalysisEvidenceNote]:
    notes: list[AnalysisEvidenceNote] = []
    for module in evidence:
        for item in module.items:
            notes.append(
                AnalysisEvidenceNote(
                    module_key=module.module,
                    rubric_item=item.rubric_item,
                    observation=item.observation.strip()[:500],
                    quotes=[quote.strip()[:280] for quote in item.quotes[:2]],
                )
            )
    return notes


def _reviewer_evidence_payload(
    evidence: list[WorkerEvidence], max_chars: int
) -> list[dict[str, object]]:
    methodology = load_methodology().definition
    by_item = {
        (module.module, note.rubric_item): note
        for module in evidence
        for note in module.items
    }
    payload: list[dict[str, object]] = [
        {
            "module": module.key,
            "rubric_item": rubric_item,
            "evidence": [],
        }
        for module in methodology.modules
        for rubric_item in module.items
    ]
    if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        raise ValueError("Reviewer evidence limit is too small for the methodology manifest")
    for entry in payload:
        note = by_item.get((str(entry["module"]), str(entry["rubric_item"])))
        if not note:
            continue
        candidate = {
            "observation": note.observation.strip()[:500],
            "quotes": [quote.strip()[:280] for quote in note.quotes[:2]],
        }
        entry["evidence"] = [candidate]
        if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
            entry["evidence"] = []
    return payload


def _parse_final_assessment(content: object, paper_text: str) -> ParsedAssessment:
    output = FinalAssessmentOutput.model_validate(_json_value(content))
    methodology = load_methodology().definition
    item_modules = {
        item: module.key for module in methodology.modules for item in module.items
    }
    findings: list[Finding] = []
    seen: set[str] = set()
    duplicate_items: set[str] = set()
    unknown_items: set[str] = set()
    assessed_attempts = 0
    grounded_assessed = 0
    for decision in output.assessments:
        rubric_item = decision.rubric_item
        if rubric_item not in item_modules:
            unknown_items.add(rubric_item)
            continue
        if rubric_item in seen:
            duplicate_items.add(rubric_item)
            continue
        seen.add(rubric_item)
        grade = decision.grade
        quotes = list(dict.fromkeys(quote.strip() for quote in decision.evidence_quotes if quote.strip()))
        grounded_quotes = [quote for quote in quotes if quote in paper_text]
        limitations: list[str] = []
        if grade != RubricGrade.NOT_ASSESSED:
            assessed_attempts += 1
            if grounded_quotes:
                grounded_assessed += 1
            else:
                grade = RubricGrade.NOT_ASSESSED
                limitations.append(
                    "The final judgment supplied no exact quote from the normalized paper."
                )
        severity = {
            RubricGrade.NO_CONCERN: FindingSeverity.INFO,
            RubricGrade.NOT_ASSESSED: FindingSeverity.INFO,
            RubricGrade.MINOR_CONCERN: FindingSeverity.MINOR,
            RubricGrade.MAJOR_CONCERN: FindingSeverity.MAJOR,
            RubricGrade.CRITICAL_CONCERN: FindingSeverity.CRITICAL,
        }[grade]
        category = item_modules[rubric_item]
        findings.append(
            Finding(
                id=hashlib.sha1(
                    f"{category}:{rubric_item}".encode(), usedforsecurity=False
                ).hexdigest()[:12],
                category=category,
                rubric_item=rubric_item,
                title=f"{rubric_item.replace('_', ' ').title()}: {grade.value.replace('_', ' ')}",
                explanation=decision.explanation,
                severity=severity,
                grade=grade,
                confidence=decision.confidence if grade != RubricGrade.NOT_ASSESSED else 0,
                paper_spans=[{"quote": quote} for quote in grounded_quotes],
                affected_conclusions=[],
                counterevidence=[],
                limitations=limitations,
                critic_disposition="accepted",
            )
        )
    missing_item_ids = sorted(set(item_modules) - seen)
    validation_warnings: list[str] = []
    if duplicate_items:
        validation_warnings.append(
            "Duplicate final methodology items were ignored: "
            + ", ".join(sorted(duplicate_items))
            + "."
        )
    if unknown_items:
        validation_warnings.append(
            "Unknown final methodology item IDs were ignored: "
            + ", ".join(sorted(unknown_items))
            + "."
        )
    return ParsedAssessment(
        findings=findings,
        summary=output.summary,
        missing_item_ids=missing_item_ids,
        assessed_attempts=assessed_attempts,
        grounded_assessed=grounded_assessed,
        validation_warnings=validation_warnings,
    )


def _model(values: dict, api_key: str, role: str) -> OpenAILike:
    model_id = values.get(f"{role}_model") or values.get("worker_model")
    return OpenAILike(
        id=model_id,
        api_key=api_key,
        base_url=values.get("base_url", "https://api.tokenfactory.nebius.com/v1/"),
        temperature=0,
        max_completion_tokens=6144 if role == "reviewer" else None,
        retries=0 if role == "reviewer" else 2,
        exponential_backoff=role != "reviewer",
        http_client=values.get("_http_client"),
    )


def _provider_for_run(
    row: AnalysisRow,
    app: AppSettings,
    provider_override: dict | None,
) -> tuple[dict, str | None, str]:
    if provider_override:
        return provider_override, provider_override.get("api_key"), str(provider_override.get("profile", "byok"))
    runtime = (row.request or {}).get("provider_runtime") or {}
    profile = runtime.get("profile", "token_factory")
    values = {
        "base_url": "https://api.tokenfactory.nebius.com/v1/",
        "worker_model": app.token_factory_worker_model,
        "reviewer_model": app.token_factory_reviewer_model,
    }
    return values, app.token_factory_api_key, profile


def classify_profile(text: str) -> RubricProfile:
    sample = text[:40000].lower()
    systematic_position = min(
        (position for term in ("systematic review", "meta-analysis") if (position := sample.find(term)) >= 0),
        default=len(sample) + 1,
    )
    randomized_position = min(
        (
            position
            for term in ("randomized", "randomised", "randomly assigned")
            if (position := sample.find(term)) >= 0
        ),
        default=len(sample) + 1,
    )
    if systematic_position < randomized_position:
        return RubricProfile.SYSTEMATIC_REVIEW
    rules = [
        (RubricProfile.RANDOMIZED, ("randomized", "randomised", "randomly assigned")),
        (RubricProfile.COMPUTATIONAL, ("machine learning", "neural network", "simulation study")),
        (RubricProfile.OBSERVATIONAL, ("cohort", "case-control", "cross-sectional", "observational")),
        (RubricProfile.SYSTEMATIC_REVIEW, ("systematic review", "meta-analysis")),
        (RubricProfile.GENERAL_EMPIRICAL, ("methods", "participants", "experiment", "dataset")),
    ]
    for profile, needles in rules:
        if any(needle in sample for needle in needles):
            return profile
    return RubricProfile.COMMON_CORE


def baseline_findings(
    text: str,
    profile: RubricProfile,
    content_level: ContentLevel = ContentLevel.FULL_TEXT,
    reason: str = "No worker model was configured.",
) -> list[Finding]:
    """Visible no-provider fallback; it never turns lexical presence into methodological credit."""
    del profile, text
    methodology = load_methodology().definition
    findings: list[Finding] = []
    for module in methodology.modules:
        if not content_allows(content_level, module.minimum_content_level):
            continue
        for item in module.items:
            findings.append(
                Finding(
                    id=hashlib.sha1(f"{module.key}:{item}".encode(), usedforsecurity=False).hexdigest()[:12],
                    category=module.key,
                    rubric_item=item,
                    title=f"{item.replace('_', ' ').title()}: not assessed",
                    explanation=f"{reason} This methodology item was not assessed.",
                    severity=FindingSeverity.INFO,
                    grade=RubricGrade.NOT_ASSESSED,
                    confidence=0.0,
                    paper_spans=[],
                    limitations=[f"Deterministic baseline; {reason}"],
                    critic_disposition="unreviewed",
                )
            )
    return findings


def _usage(response: object) -> dict[str, int]:
    metrics = getattr(response, "metrics", None)
    if not metrics:
        return {}
    result: dict[str, int] = {}
    for source, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
        value = getattr(metrics, source, None)
        if isinstance(value, int):
            result[target] = value
    return result


async def llm_evidence(
    text: str,
    profile: RubricProfile,
    content_level: ContentLevel,
    values: dict,
    key: str,
    sequential: bool,
    on_module_event: Callable[
        [str, str, str, int, str | None, list[AnalysisEvidenceNote]], None
    ]
    | None = None,
) -> tuple[list[WorkerEvidence], dict[str, str], dict[str, int]]:
    methodology = load_methodology()
    routing = methodology.definition.routing
    chunks = chunk_document(text, routing.target_chunk_chars, routing.overlap_chars)

    async def run_one(module) -> tuple[WorkerEvidence | None, str | None, dict[str, int]]:
        if not content_allows(content_level, module.minimum_content_level):
            return None, None, {}
        if on_module_event:
            on_module_event(module.key, module.label, "running", 0, None, [])
        routed = route_chunks(chunks, module, routing.max_chunks_per_module)
        agent = Agent(
            name=module.label,
            model=_model(values, key, "worker"),
            output_schema=WorkerEvidenceOutput,
            structured_outputs=True,
            parse_response=False,
            instructions=[
                methodology.worker_prompt,
                f"Module={module.key}. Expected rubric_item values: {', '.join(module.items)}.",
                "Prefer a JSON object with an `evidence` array. Each entry should contain "
                "rubric_item, observation, and exact quotes. If JSON is difficult, return clear "
                "text notes instead. Do not assign grades or scores.",
            ],
        )
        prompt = (
            f"Paper profile: {profile.value}. Module: {module.label}. "
            f"{rubric_prompt(profile)} Analyze only the routed evidence below.\n"
            + format_routed_chunks(routed)[: routing.max_module_chars]
        )
        try:
            response = await agent.arun(prompt)
            evidence = _coerce_worker_evidence(response.content, module.key, module.items, text)
            if not evidence.raw_text:
                raise ValueError("worker returned no evidence notes")
            return evidence, None, _usage(response)
        except Exception as exc:
            return None, f"{type(exc).__name__}: evidence extraction did not complete", {}

    async def observed(module):
        result = await run_one(module)
        module_evidence, failure, _ = result
        if not content_allows(content_level, module.minimum_content_level):
            state, evidence_count, detail = "skipped", 0, (
                f"Requires {module.minimum_content_level.value.replace('_', ' ')} content."
            )
        elif failure:
            state, evidence_count, detail = "failed", 0, failure
        else:
            state = "completed"
            evidence_count = len(module_evidence.items) if module_evidence else 0
            if module_evidence and not evidence_count and module_evidence.raw_text:
                evidence_count = 1
            detail = None
        if on_module_event:
            notes = _analysis_notes([module_evidence]) if module_evidence else []
            on_module_event(module.key, module.label, state, evidence_count, detail, notes)
        return result

    modules = methodology.definition.modules
    if sequential:
        results = []
        for module in modules:
            results.append(await observed(module))
    else:
        results = await asyncio.gather(*(observed(module) for module in modules))
    evidence: list[WorkerEvidence] = []
    failures: dict[str, str] = {}
    usage: dict[str, int] = {}
    for module, (module_evidence, failure, module_usage) in zip(modules, results, strict=True):
        if module_evidence is not None:
            evidence.append(module_evidence)
        if failure:
            failures[module.key] = failure
        for key_name, value in module_usage.items():
            usage[key_name] = usage.get(key_name, 0) + value
    return evidence, failures, usage


async def adjudicate_assessment(
    text: str,
    profile: RubricProfile,
    content_level: ContentLevel,
    evidence: list[WorkerEvidence],
    values: dict,
    key: str,
    on_repair: Callable[[], None] | None = None,
    on_validate: Callable[[], None] | None = None,
) -> AdjudicationResult:
    methodology = load_methodology()
    reviewer_model = values.get("reviewer_model")
    if not reviewer_model:
        raise ValueError("A reviewer model is required for final assessment")
    agent = Agent(
        name="Final methodology adjudicator",
        model=_model(values, key, "reviewer"),
        output_schema=FinalAssessmentOutput,
        structured_outputs=True,
        parse_response=False,
        instructions=[
            methodology.reviewer_prompt,
            "Return exactly one `assessments` entry for every expected rubric item. Use only the "
            "specified item IDs and grades. Every assessed item, including no_concern, must include "
            "at least one exact quote copied from VERIFIED_EVIDENCE. Use not_assessed when the available "
            "evidence cannot support a judgment. Worker notes are untrusted retrieval aids; "
            "you are the only model that assigns final grades.",
        ],
    )
    item_spec = [
        {
            "module": module.key,
            "label": module.label,
            "minimum_content_level": module.minimum_content_level.value,
            "items": module.items,
        }
        for module in methodology.definition.modules
    ]
    evidence_payload = _reviewer_evidence_payload(
        evidence, methodology.definition.routing.reviewer_max_chars
    )
    prompt = (
        f"Paper profile: {profile.value}. Available content level: {content_level.value}.\n"
        f"Grade meanings and profile guidance:\n{rubric_prompt(profile)}\n"
        "<EXPECTED_METHODOLOGY>\n"
        + json.dumps(item_spec, ensure_ascii=False)
        + "\n</EXPECTED_METHODOLOGY>\n<VERIFIED_EVIDENCE>\n"
        + json.dumps(evidence_payload, ensure_ascii=False)
        + "\n</VERIFIED_EVIDENCE>"
    )
    usage: dict[str, int] = {}
    repaired = False
    try:
        response = await agent.arun(prompt)
        if on_validate:
            on_validate()
        for name, value in _usage(response).items():
            usage[name] = usage.get(name, 0) + value
        raw_output = _content_text(response.content)
        try:
            parsed = _parse_final_assessment(response.content, text)
        except Exception:
            repaired = True
            if on_repair:
                on_repair()
            repair_agent = Agent(
                name="Final assessment formatter",
                model=_model(values, key, "reviewer"),
                output_schema=FinalAssessmentOutput,
                structured_outputs=True,
                parse_response=False,
                instructions=[
                    "Reformat the supplied model response into the required JSON schema. Preserve "
                    "its judgments, explanations, item IDs, quotes, and omissions exactly. Do not "
                    "perform new analysis, add missing methodology items, or invent evidence."
                ],
            )
            repair_response = await repair_agent.arun(
                "<ORIGINAL_RESPONSE>\n" + raw_output + "\n</ORIGINAL_RESPONSE>"
            )
            for name, value in _usage(repair_response).items():
                usage[name] = usage.get(name, 0) + value
            parsed = _parse_final_assessment(repair_response.content, text)
    except Exception as exc:
        raise ValueError(f"Final assessment did not complete: {type(exc).__name__}") from exc
    return AdjudicationResult(
        findings=parsed.findings,
        summary=parsed.summary,
        missing_item_ids=parsed.missing_item_ids,
        repaired_output=repaired,
        assessed_attempts=parsed.assessed_attempts,
        grounded_assessed=parsed.grounded_assessed,
        validation_warnings=parsed.validation_warnings,
        usage=usage,
    )


async def _fetch_source(
    row: AnalysisRow, db: Session, app: AppSettings
) -> tuple[str, PaperIdentity, dict, ContentLevel, SourceFormat, str, str, str]:
    source = row.source
    if source.get("kind") != "document":
        raise ValueError("Analysis requires a canonical PaperDocument")
    document_row = db.get(DocumentRow, source["value"])
    if not document_row:
        raise ValueError("Parsed document was not found or has expired")
    document = get_document_store(app).get(document_row.object_key)
    text = document.text
    identity = document.identity
    identity.fingerprint = identity.fingerprint or fingerprint_text(text)
    return (
        text,
        identity,
        {"extraction_warnings": document.extraction_warnings},
        document.content_level,
        document.source_format,
        document.parser_name,
        document.parser_version,
        document.sha256,
    )


def _event(
    row: AnalysisRow,
    label: str,
    progress: int,
    *,
    kind: str = "stage",
    state: str = "running",
    key: str | None = None,
    evidence_count: int = 0,
    notes: list[AnalysisEvidenceNote] | None = None,
    detail: str | None = None,
) -> None:
    events = [dict(event) for event in row.events or []]
    if kind == "stage":
        for event in reversed(events):
            if event.get("kind", "stage") == "stage" and event.get("state", "running") == "running":
                event["state"] = "completed"
                break
        row.stage = label
        row.progress = progress
    events.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "key": key,
            "label": label,
            "state": state,
            "progress": progress,
            "evidence_count": evidence_count,
            "notes": [note.model_dump(mode="json") for note in notes or []],
            "detail": detail,
        }
    )
    row.events = events


async def _await_active_reviewer(
    awaitable: Coroutine[object, object, AdjudicationResult],
    row: AnalysisRow,
    db: Session,
    deadline_seconds: float,
) -> AdjudicationResult:
    task = asyncio.create_task(awaitable)
    deadline = asyncio.get_running_loop().time() + deadline_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise TimeoutError("Final reviewer exceeded its total deadline")
        done, _ = await asyncio.wait({task}, timeout=min(1.0, remaining))
        if done:
            return task.result()
        db.refresh(row, attribute_names=["cancel_requested"])
        if row.cancel_requested:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise AnalysisCancelled


async def execute_analysis(
    analysis_id: str,
    db: Session,
    app: AppSettings,
    provider_override: dict | None = None,
) -> None:
    row = db.get(AnalysisRow, analysis_id)
    if not row:
        return
    provider_http_client: httpx.AsyncClient | None = None
    try:
        row.state = "running"
        _event(row, "Ingesting and fingerprinting", 8)
        db.commit()
        text, identity, metadata, content_level, source_format, parser_name, parser_version, paper_sha = await _fetch_source(row, db, app)
        if row.cancel_requested:
            row.state = "cancelled"
            _event(row, "Cancelled", row.progress, state="cancelled")
            db.commit()
            return

        _event(row, "Classifying paper and routing evidence", 22)
        profile = classify_profile(text)
        db.commit()
        values, api_key, provider_profile = _provider_for_run(row, app, provider_override)
        if api_key:
            async def validate_provider_request(request: httpx.Request) -> None:
                validate_public_url(str(request.url))

            provider_http_client = httpx.AsyncClient(
                timeout=app.provider_timeout_seconds,
                follow_redirects=False,
                event_hooks={"request": [validate_provider_request]},
            )
            values["_http_client"] = provider_http_client
        _event(row, "Gathering methodology evidence", 35)
        db.commit()
        token_usage: dict[str, int] = {}
        modules = load_methodology().definition.modules
        module_count = len(modules)
        completed_modules = 0

        for module in modules:
            _event(
                row,
                module.label,
                35,
                kind="module",
                state="pending",
                key=module.key,
                detail="Waiting to start.",
            )
        db.commit()

        def record_module(
            key: str,
            label: str,
            state: str,
            evidence_count: int,
            detail: str | None,
            notes: list[AnalysisEvidenceNote],
        ) -> None:
            nonlocal completed_modules
            if state in {"completed", "failed", "skipped"}:
                completed_modules += 1
            progress = 35 + round((completed_modules / module_count) * 33)
            _event(
                row,
                label,
                progress,
                kind="module",
                state=state,
                key=key,
                evidence_count=evidence_count,
                notes=notes,
                detail=detail,
            )
            row.progress = progress
            db.commit()

        if api_key and values.get("worker_model"):
            worker_evidence, evidence_failures, worker_usage = await llm_evidence(
                text,
                profile,
                content_level,
                values,
                api_key,
                row.request.get("sequential", False),
                record_module,
            )
            token_usage.update(worker_usage)
        else:
            worker_evidence = []
            evidence_failures = {
                module.key: "No worker model was configured for this hosted profile."
                for module in modules
                if content_allows(content_level, module.minimum_content_level)
            }
            for module in modules:
                eligible = content_allows(content_level, module.minimum_content_level)
                record_module(
                    module.key,
                    module.label,
                    "failed" if eligible else "skipped",
                    0,
                    evidence_failures.get(module.key)
                    or f"Requires {module.minimum_content_level.value.replace('_', ' ')} content.",
                    [],
                )

        db.refresh(row, attribute_names=["cancel_requested"])
        if row.cancel_requested:
            raise AnalysisCancelled

        _event(row, "Checking current-paper scholarly context", 72)
        db.commit()
        context = ContextAssessment()
        limitations = [
            "The Review score summarizes assessed methodology items; it is not the probability that the conclusions are true.",
            "No cited-paper full texts were retrieved or analyzed.",
            "English is validated first; other languages are experimental.",
        ]
        limitations.extend(str(item) for item in metadata.get("extraction_warnings", []))
        source_fallback_warnings = [
            str(item)
            for item in metadata.get("extraction_warnings", [])
            if "could not be used; analysis used" in str(item)
        ]
        if identity.doi:
            client = EvidenceClient(app.upstream_timeout_seconds)
            try:
                crossref = await client.crossref(identity.doi)
            finally:
                await client.close()
            if crossref.available:
                updates = crossref.data.get("update-to", [])
                update_text = [json.dumps(item).lower() if isinstance(item, dict) else str(item).lower() for item in updates]
                context.retracted = any("retract" in item for item in update_text)
                context.expression_of_concern = any("concern" in item for item in update_text)
                context.corrections = [item[:300] for item in update_text if "correct" in item]
                if context.retracted or context.expression_of_concern or context.corrections:
                    context.record_sources.append(
                        EvidenceSource(
                            title="Crossref publication record",
                            url=f"https://api.crossref.org/works/{identity.doi}",
                            publisher="Crossref",
                            accessed_at=datetime.now(UTC),
                            identifier=identity.doi,
                        )
                    )
            else:
                limitations.append(crossref.limitation or "Crossref context unavailable.")
        else:
            limitations.append("No DOI was resolved; publication-record context is limited.")

        _event(
            row,
            "Generating final assessment",
            84,
            detail="Single bounded reviewer attempt; maximum 6,144 completion tokens.",
        )
        db.commit()
        summary: list[str] = []
        reviewer_completed = False
        missing_item_ids: list[str] = []
        repaired_output = False
        assessed_attempts = 0
        grounded_assessed = 0
        adjudication_warnings: list[str] = []
        reviewer_failure_warning: str | None = None
        if api_key and values.get("reviewer_model"):
            def record_repair() -> None:
                _event(
                    row,
                    "Repairing response format",
                    92,
                    detail="One schema-only repair attempt; no new analysis.",
                )
                db.commit()

            def record_validation() -> None:
                _event(
                    row,
                    "Validating reviewer response",
                    90,
                    detail="Checking methodology coverage and exact-quote grounding.",
                )
                db.commit()

            try:
                adjudication = await _await_active_reviewer(
                    adjudicate_assessment(
                        text,
                        profile,
                        content_level,
                        worker_evidence,
                        values,
                        api_key,
                        record_repair,
                        record_validation,
                    ),
                    row,
                    db,
                    app.reviewer_deadline_seconds,
                )
                findings = adjudication.findings
                summary = adjudication.summary
                reviewer_completed = True
                missing_item_ids = adjudication.missing_item_ids
                repaired_output = adjudication.repaired_output
                assessed_attempts = adjudication.assessed_attempts
                grounded_assessed = adjudication.grounded_assessed
                adjudication_warnings = adjudication.validation_warnings
                for key_name, value in adjudication.usage.items():
                    token_usage[key_name] = token_usage.get(key_name, 0) + value
                if not any(finding.grade != RubricGrade.NOT_ASSESSED for finding in findings):
                    raise ValueError("Final assessment produced no evidence-grounded grades")
            except AnalysisCancelled:
                raise
            except Exception as exc:
                safe_failure = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    and str(exc).startswith("Final assessment did not complete:")
                    else type(exc).__name__
                )
                reviewer_failure_warning = (
                    f"Final reviewer did not complete ({safe_failure}); "
                    "this provisional report contains no final methodology grades."
                )
                _event(
                    row,
                    "Final reviewer unavailable",
                    94,
                    state="failed",
                    detail=f"{safe_failure}; preparing a provisional report.",
                )
                db.commit()
                findings = baseline_findings(
                    text,
                    profile,
                    content_level,
                    reason="The final reviewer did not complete.",
                )
        else:
            findings = baseline_findings(text, profile, content_level)

        bundle = load_methodology()
        missing_by_module: dict[str, str] = {}
        for module in bundle.definition.modules:
            missing = [item for item in module.items if item in missing_item_ids]
            if missing:
                missing_by_module[module.key] = (
                    "Final assessment omitted: " + ", ".join(missing) + "."
                )
        score = score_findings(
            findings, context, content_level, missing_by_module, reviewer_completed
        )
        eligible_modules = [
            module
            for module in bundle.definition.modules
            if content_allows(content_level, module.minimum_content_level)
        ]
        successful_evidence_modules = sum(
            module.key not in evidence_failures for module in eligible_modules
        )
        evidence_module_coverage = (
            successful_evidence_modules / len(eligible_modules) if eligible_modules else 0.0
        )
        quote_grounding_rate = (
            grounded_assessed / assessed_attempts if assessed_attempts else 0.0
        )
        confidence_components = ConfidenceComponents(
            assessment_coverage=score.weighted_coverage,
            evidence_module_coverage=round(evidence_module_coverage, 3),
            quote_grounding_rate=round(quote_grounding_rate, 3),
        )
        confidence_score = round(
            confidence_components.assessment_coverage
            * confidence_components.evidence_module_coverage
            * confidence_components.quote_grounding_rate
            * 100,
            1,
        )
        assessed_item_count = sum(
            finding.grade != RubricGrade.NOT_ASSESSED for finding in findings
        )
        execution_warnings: list[str] = []
        execution_warnings.extend(source_fallback_warnings)
        execution_warnings.extend(adjudication_warnings)
        if reviewer_failure_warning:
            execution_warnings.append(reviewer_failure_warning)
        if evidence_failures:
            execution_warnings.append(
                "Worker evidence extraction was unavailable for: "
                + ", ".join(sorted(evidence_failures))
                + "; those items had no module evidence for final adjudication."
            )
        if repaired_output:
            execution_warnings.append(
                "The final model response required one schema-format repair pass."
            )
        if missing_item_ids:
            execution_warnings.append(
                "The final response omitted methodology items: "
                + ", ".join(missing_item_ids)
                + "."
            )
        unsupported_attempts = assessed_attempts - grounded_assessed
        if unsupported_attempts:
            execution_warnings.append(
                f"{unsupported_attempts} attempted judgments lacked an exact normalized-paper "
                "quote and were excluded from scoring."
            )
        if not reviewer_completed and not reviewer_failure_warning:
            execution_warnings.append(
                "No final adjudicator model was configured; deterministic not-assessed entries "
                "are shown for compatibility."
            )
        if not summary:
            summary = [
                f"This {content_level.value.replace('_', ' ')} review assessed {sum(item.assessed_items for item in score.module_statuses)} of {sum(item.expected_items for item in score.module_statuses)} standard methodology items.",
                "Open the evidence ledger before drawing conclusions from any automated concern.",
            ]
        report = AnalysisReport(
            id=UUID(row.id),
            identity=identity,
            profile=profile,
            language="en",
            content_level=content_level,
            source_format=source_format,
            review_score=score.composite,
            composite_score=score.composite,
            uncapped_score=score.uncapped,
            dimensions=score.dimensions,
            coverage=score.coverage,
            confidence_score=confidence_score,
            confidence_components=confidence_components,
            assessed_item_count=assessed_item_count,
            missing_item_ids=missing_item_ids,
            failed_evidence_modules=sorted(evidence_failures),
            repaired_output=repaired_output,
            execution_warnings=execution_warnings,
            evidence_notes=_analysis_notes(worker_evidence),
            evidence_verification_rate=round(quote_grounding_rate, 3),
            context=context,
            module_statuses=score.module_statuses,
            findings=findings,
            summary=summary,
            banners=score.banners,
            limitations=[*limitations, *score.coverage.limitations, *execution_warnings],
            audit_trail=[*row.events, {"at": datetime.now(UTC).isoformat(), "stage": "Scored", "progress": 96}],
            methodology_version=bundle.definition.version,
            methodology_hash=bundle.bundle_hash,
            parser_name=parser_name,
            parser_version=parser_version,
            provider_profile=provider_profile,
            provider_protocol="openai-compatible",
            worker_model=values.get("worker_model", ""),
            reviewer_model=values.get("reviewer_model", ""),
            token_usage=token_usage,
            paper_sha256=paper_sha,
            completed_at=datetime.now(UTC),
        )
        row.report = report.model_dump(mode="json")
        row.state = "completed"
        _event(row, "Complete", 100, state="completed")
        db.commit()
        document_row = db.get(DocumentRow, row.source.get("value"))
        if document_row:
            try:
                get_document_store(app).delete(document_row.object_key)
                db.delete(document_row)
                db.commit()
            except Exception:
                db.rollback()
    except AnalysisCancelled:
        row.state = "cancelled"
        _event(row, "Cancelled", row.progress, state="cancelled")
        db.commit()
    except Exception as exc:
        row.state = "failed"
        row.error = str(exc)[:1000]
        _event(row, "Analysis failed", row.progress, state="failed", detail=row.error)
        db.commit()
    finally:
        if provider_http_client:
            await provider_http_client.aclose()
