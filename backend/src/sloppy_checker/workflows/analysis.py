from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.database import AnalysisRow, DocumentRow
from sloppy_checker.core.ingest import fingerprint_text
from sloppy_checker.core.methodology import content_allows, load_methodology
from sloppy_checker.core.rubrics import rubric_items, rubric_prompt
from sloppy_checker.core.schemas import (
    AnalysisEvidenceNote,
    AnalysisReport,
    ConfidenceComponents,
    ContentLevel,
    ContextAssessment,
    EvidenceSource,
    Finding,
    FindingSeverity,
    PaperDocument,
    PaperSpan,
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
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_state: Literal["observed", "not_found", "ambiguous"] = "ambiguous"


class WorkerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str
    items: list[EvidenceNote] = Field(default_factory=list)


class WorkerNoteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_item: str
    observation: str
    quotes: list[str] = Field(default_factory=list, max_length=8)
    evidence_state: Literal["observed", "not_found", "ambiguous"] = "ambiguous"


class WorkerEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[WorkerNoteOutput]


class FinalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_item: str
    grade: RubricGrade
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class FinalAssessmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[FinalDecision]


@dataclass(frozen=True)
class VerifiedEvidence:
    id: str
    module_key: str
    rubric_item: str
    evidence_state: Literal["observed", "not_found", "ambiguous"]
    quote: str
    paper_span: PaperSpan


@dataclass(frozen=True)
class ParsedAssessment:
    findings: list[Finding]
    missing_item_ids: list[str]
    assessed_attempts: int
    grounded_assessed: int
    validation_warnings: list[str]


@dataclass(frozen=True)
class AdjudicationResult:
    findings: list[Finding]
    missing_item_ids: list[str]
    repaired_output: bool
    assessed_attempts: int
    grounded_assessed: int
    validation_warnings: list[str]
    usage: dict[str, int]


class AnalysisCancelled(Exception):
    """Raised when a durable cancellation request interrupts an active model call."""


def _derived_summary(findings: list[Finding], assessed: int, expected: int) -> list[str]:
    severity_order = {
        FindingSeverity.CRITICAL: 0,
        FindingSeverity.MAJOR: 1,
        FindingSeverity.MINOR: 2,
        FindingSeverity.INFO: 3,
    }
    concerns = sorted(
        (
            finding
            for finding in findings
            if finding.grade
            in {
                RubricGrade.MINOR_CONCERN,
                RubricGrade.MAJOR_CONCERN,
                RubricGrade.CRITICAL_CONCERN,
            }
            and finding.paper_spans
        ),
        key=lambda finding: (severity_order[finding.severity], finding.category, finding.rubric_item),
    )
    summary = [
        f"{finding.title}. {finding.explanation.strip()[:280]}" for finding in concerns[:4]
    ]
    summary.append(f"The final review assessed {assessed} of {expected} methodology items.")
    if not concerns:
        summary.insert(
            0,
            "No evidence-grounded methodological concern was accepted in the assessed items.",
        )
    return summary[:6]


def _json_value(content: object) -> object:
    if isinstance(content, BaseModel):
        return content.model_dump(mode="json")
    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
        return json.loads(cleaned)
    return content


def _ensure_document(document_or_text: PaperDocument | str) -> PaperDocument:
    if isinstance(document_or_text, PaperDocument):
        return document_or_text
    return PaperDocument(
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.PDF,
        sha256=hashlib.sha256(document_or_text.encode()).hexdigest(),
        parser_name="plain-text-test-adapter",
        parser_version="1",
        text=document_or_text,
    )


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


def _page_and_section(
    document: PaperDocument | None, start: int
) -> tuple[int | None, str | None]:
    if not document:
        return None, None
    page = next(
        (item.number for item in document.pages if item.start <= start < item.end),
        None,
    )
    sections = [
        item for item in document.sections if item.start <= start < item.end
    ]
    section = (
        min(sections, key=lambda item: item.end - item.start).title
        if sections
        else None
    )
    return page, section


def _verified_quote(
    quote: str,
    document_or_text: PaperDocument | str,
    module_key: str,
    rubric_item: str,
    evidence_state: Literal["observed", "not_found", "ambiguous"],
) -> VerifiedEvidence | None:
    document = document_or_text if isinstance(document_or_text, PaperDocument) else None
    text = document.text if document else document_or_text
    grounded = _ground_quote(quote, text)
    if not grounded:
        return None
    start = text.find(grounded)
    end = start + len(grounded)
    page, section = _page_and_section(document, start)
    evidence_id = hashlib.sha1(
        f"{module_key}:{rubric_item}:{evidence_state}:{start}:{end}:{grounded}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return VerifiedEvidence(
        id=f"span-{evidence_id}",
        module_key=module_key,
        rubric_item=rubric_item,
        evidence_state=evidence_state,
        quote=grounded,
        paper_span=PaperSpan(
            quote=grounded,
            start=start,
            end=end,
            page=page,
            section=section,
        ),
    )


def _parse_worker_evidence(
    content: object,
    module_key: str,
    expected_items: tuple[str, ...],
    document_or_text: PaperDocument | str,
) -> WorkerEvidence:
    output = WorkerEvidenceOutput.model_validate(_json_value(content))
    by_item: dict[str, EvidenceNote] = {}
    for record in output.evidence:
        if record.rubric_item not in expected_items:
            raise ValueError(f"unknown rubric item: {record.rubric_item}")
        if record.rubric_item in by_item:
            raise ValueError(f"duplicate rubric item: {record.rubric_item}")
        verified = [
            item
            for quote in record.quotes
            if (
                item := _verified_quote(
                    quote,
                    document_or_text,
                    module_key,
                    record.rubric_item,
                    record.evidence_state,
                )
            )
            is not None
        ]
        evidence_state = record.evidence_state
        observation = record.observation
        if evidence_state == "observed" and not verified:
            evidence_state = "ambiguous"
            observation = (
                "The worker returned an observation, but its quote did not match the "
                "normalized paper exactly."
            )
        if evidence_state != "observed":
            verified = []
        by_item[record.rubric_item] = (
            EvidenceNote(
                rubric_item=record.rubric_item,
                observation=observation,
                quotes=list(dict.fromkeys(item.quote for item in verified)),
                evidence_ids=list(dict.fromkeys(item.id for item in verified)),
                evidence_state=evidence_state,
            )
        )
    items = [
        by_item.get(item)
        or EvidenceNote(
            rubric_item=item,
            observation="The worker returned no extraction note for this item.",
            evidence_state="ambiguous",
        )
        for item in expected_items
    ]
    return WorkerEvidence(module=module_key, items=items)


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
                    evidence_state=item.evidence_state,
                )
            )
    return notes


def _evidence_registry(
    evidence: list[WorkerEvidence], document: PaperDocument
) -> dict[str, VerifiedEvidence]:
    registry: dict[str, VerifiedEvidence] = {}
    for module in evidence:
        for note in module.items:
            if note.evidence_state != "observed":
                continue
            for evidence_id, quote in zip(note.evidence_ids, note.quotes, strict=False):
                verified = _verified_quote(
                    quote,
                    document,
                    module.module,
                    note.rubric_item,
                    note.evidence_state,
                )
                if verified and verified.id == evidence_id:
                    registry[evidence_id] = verified
    return registry


def _reviewer_evidence_payload(
    evidence: list[WorkerEvidence],
    profile: RubricProfile,
    max_chars: int,
    registry: dict[str, VerifiedEvidence],
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
        for rubric_item in rubric_items(profile, module.key, module.items)
    ]
    if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        raise ValueError("Reviewer evidence limit is too small for the methodology manifest")
    for entry in payload:
        note = by_item.get((str(entry["module"]), str(entry["rubric_item"])))
        if not note or note.evidence_state != "observed":
            continue
        cited_evidence: list[dict[str, str]] = []
        for evidence_id in note.evidence_ids:
            ref = registry.get(evidence_id)
            if (
                ref is None
                or ref.module_key != entry["module"]
                or ref.rubric_item != entry["rubric_item"]
                or ref.evidence_state != "observed"
            ):
                continue
            cited_evidence.append(
                {
                    "id": evidence_id,
                    "quote": ref.quote[:280],
                }
            )
        if cited_evidence:
            entry["observation"] = note.observation[:500]
            entry["evidence"] = cited_evidence
        if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
            entry.pop("observation", None)
            entry["evidence"] = []
    return payload


def _source_quality(document: PaperDocument) -> float:
    return {
        SourceFormat.JATS: 1.0,
        SourceFormat.PDF: 0.9,
        SourceFormat.HTML: 0.85,
        SourceFormat.ABSTRACT: 0.65,
        SourceFormat.METADATA: 0.45,
    }[document.source_format]


def _parse_final_assessment(
    content: object,
    document: PaperDocument,
    registry: dict[str, VerifiedEvidence],
    profile: RubricProfile = RubricProfile.GENERAL_EMPIRICAL,
) -> ParsedAssessment:
    output = FinalAssessmentOutput.model_validate(_json_value(content))
    methodology = load_methodology().definition
    item_modules = {
        item: module.key
        for module in methodology.modules
        for item in rubric_items(profile, module.key, module.items)
    }
    findings: list[Finding] = []
    seen: set[str] = set()
    duplicate_items: set[str] = set()
    unknown_items: set[str] = set()
    unknown_evidence_ids: set[str] = set()
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
        evidence_ids = list(dict.fromkeys(decision.evidence_ids))
        unknown_evidence_ids.update(item for item in evidence_ids if item not in registry)
        category = item_modules[rubric_item]
        references = [
            registry[item]
            for item in evidence_ids
            if item in registry
            and registry[item].module_key == category
            and registry[item].rubric_item == rubric_item
            and registry[item].evidence_state == "observed"
        ]
        spans = [item.paper_span for item in references]
        limitations: list[str] = []
        if grade != RubricGrade.NOT_ASSESSED:
            assessed_attempts += 1
            if spans:
                grounded_assessed += 1
            else:
                grade = RubricGrade.NOT_ASSESSED
                limitations.append(
                    "The final judgment cited no matching observed paper evidence."
                )
        severity = {
            RubricGrade.NO_CONCERN: FindingSeverity.INFO,
            RubricGrade.NOT_ASSESSED: FindingSeverity.INFO,
            RubricGrade.MINOR_CONCERN: FindingSeverity.MINOR,
            RubricGrade.MAJOR_CONCERN: FindingSeverity.MAJOR,
            RubricGrade.CRITICAL_CONCERN: FindingSeverity.CRITICAL,
        }[grade]
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
                confidence=(
                    round(_source_quality(document) * (1.0 if spans else 0.7), 2)
                    if grade != RubricGrade.NOT_ASSESSED
                    else 0
                ),
                paper_spans=spans,
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
    if unknown_evidence_ids:
        validation_warnings.append(
            f"{len(unknown_evidence_ids)} unknown reviewer evidence IDs were ignored."
        )
    return ParsedAssessment(
        findings=findings,
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
        (
            RubricProfile.COMPUTATIONAL,
            (
                "machine learning",
                "neural network",
                "simulation study",
                "transformer",
                "language model",
                "benchmark",
                "algorithm",
            ),
        ),
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
    del text
    methodology = load_methodology().definition
    findings: list[Finding] = []
    for module in methodology.modules:
        if not content_allows(content_level, module.minimum_content_level):
            continue
        for item in rubric_items(profile, module.key, module.items):
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


def _safe_module_failure(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        fields = [
            ".".join(str(part) for part in error.get("loc", ()))
            + ":"
            + str(error.get("type", "invalid"))
            for error in exc.errors(include_url=False, include_context=False, include_input=False)[:4]
        ]
        return "Invalid structured output (" + ", ".join(fields) + ")."
    if isinstance(exc, ValueError) and str(exc).startswith(
        ("unknown rubric item:", "duplicate rubric item:")
    ):
        return str(exc)[:200] + "."
    return f"{type(exc).__name__}: evidence extraction did not complete"


async def llm_evidence(
    document: PaperDocument | str,
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
    canonical_document = _ensure_document(document)
    chunks = chunk_document(canonical_document, routing.target_chunk_chars, routing.overlap_chars)

    async def run_one(module) -> tuple[WorkerEvidence | None, str | None, dict[str, int]]:
        if not content_allows(content_level, module.minimum_content_level):
            return None, None, {}
        if on_module_event:
            on_module_event(module.key, module.label, "running", 0, None, [])
        routed = route_chunks(
            chunks, module, routing.max_chunks_per_module, canonical_document
        )
        expected_items = rubric_items(profile, module.key, module.items)
        agent = Agent(
            name=module.label,
            model=_model(values, key, "worker"),
            output_schema=WorkerEvidenceOutput,
            structured_outputs=True,
            parse_response=False,
            instructions=[
                methodology.worker_prompt,
                f"Module={module.key}. Expected rubric_item values: {', '.join(expected_items)}.",
                "Return a JSON object with an `evidence` array. Each entry must contain only "
                "rubric_item, observation, evidence_state (observed, not_found, or ambiguous), "
                "and exact quotes. Every observed item must include at least one exact quote; "
                "use ambiguous when you cannot copy a supporting quote. The observation may state "
                "only what its quotes establish; put the strongest direct quote first and return no "
                "more than two. Do not assign grades or scores.",
            ],
        )
        prompt = (
            f"Paper profile: {profile.value}. Module: {module.label}. "
            f"Retrieve evidence only for these rubric items: {', '.join(expected_items)}. "
            "Ignore every other methodology item, even when it appears relevant. "
            "Analyze only the routed evidence below.\n"
            + format_routed_chunks(routed)[: routing.max_module_chars]
        )
        try:
            response = await agent.arun(prompt)
            evidence = _parse_worker_evidence(
                response.content,
                module.key,
                expected_items,
                canonical_document,
            )
            return evidence, None, _usage(response)
        except Exception as exc:
            return None, _safe_module_failure(exc), {}

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
    document: PaperDocument | str,
    profile: RubricProfile,
    content_level: ContentLevel,
    evidence: list[WorkerEvidence],
    values: dict,
    key: str,
    on_repair: Callable[[], None] | None = None,
    on_validate: Callable[[], None] | None = None,
) -> AdjudicationResult:
    methodology = load_methodology()
    document = _ensure_document(document)
    reviewer_model = values.get("reviewer_model")
    if not reviewer_model:
        raise ValueError("A reviewer model is required for final assessment")
    registry = _evidence_registry(evidence, document)
    agent = Agent(
        name="Final methodology adjudicator",
        model=_model(values, key, "reviewer"),
        output_schema=FinalAssessmentOutput,
        structured_outputs=True,
        parse_response=False,
        instructions=[
            methodology.reviewer_prompt,
            "Return exactly one `assessments` entry for every expected rubric item. Use only the "
            "specified item IDs and grades. Every assessed item must cite verified evidence_ids. "
            "Only observed evidence for the same module and rubric item can support a grade. "
            "A not_found or ambiguous note is never gradable. Use not_assessed only when the "
            "verified evidence ledger for the item is empty. Every item with a non-empty verified "
            "evidence ledger must receive no_concern, minor_concern, major_concern, or "
            "critical_concern. "
            "When an exact observed quote directly establishes the item—for example multiple "
            "benchmarks, an explicit data/code statement, a funding statement, or a conflict "
            "statement—grade that bounded criterion without demanding evidence about unrelated "
            "completeness. Mere silence cannot create a concern; an explicit quoted limitation can. "
            "Worker observations are untrusted retrieval aids; "
            "you are the only model that assigns final grades.",
        ],
    )
    item_spec = [
        {
            "module": module.key,
            "label": module.label,
            "minimum_content_level": module.minimum_content_level.value,
            "items": rubric_items(profile, module.key, module.items),
        }
        for module in methodology.definition.modules
    ]
    evidence_payload = _reviewer_evidence_payload(
        evidence, profile, methodology.definition.routing.reviewer_max_chars, registry
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
            parsed = _parse_final_assessment(response.content, document, registry, profile)
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
                    "its judgments, explanations, item IDs, evidence IDs, and omissions exactly. Do not "
                    "perform new analysis, add missing methodology items, or invent evidence."
                ],
            )
            repair_response = await repair_agent.arun(
                "<ORIGINAL_RESPONSE>\n" + raw_output + "\n</ORIGINAL_RESPONSE>"
            )
            for name, value in _usage(repair_response).items():
                usage[name] = usage.get(name, 0) + value
            parsed = _parse_final_assessment(
                repair_response.content, document, registry, profile
            )
    except Exception as exc:
        raise ValueError(f"Final assessment did not complete: {type(exc).__name__}") from exc
    return AdjudicationResult(
        findings=parsed.findings,
        missing_item_ids=parsed.missing_item_ids,
        repaired_output=repaired,
        assessed_attempts=parsed.assessed_attempts,
        grounded_assessed=parsed.grounded_assessed,
        validation_warnings=parsed.validation_warnings,
        usage=usage,
    )


async def _fetch_source(
    row: AnalysisRow, db: Session, app: AppSettings
) -> PaperDocument:
    source = row.source
    if source.get("kind") != "document":
        raise ValueError("Analysis requires a canonical PaperDocument")
    document_row = db.get(DocumentRow, source["value"])
    if not document_row:
        raise ValueError("Parsed document was not found or has expired")
    document = get_document_store(app).get(document_row.object_key)
    document.identity.fingerprint = document.identity.fingerprint or fingerprint_text(
        document.text
    )
    return document


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
        document = await _fetch_source(row, db, app)
        text = document.text
        identity = document.identity
        metadata = {"extraction_warnings": document.extraction_warnings}
        content_level = document.content_level
        source_format = document.source_format
        parser_name = document.parser_name
        parser_version = document.parser_version
        paper_sha = document.sha256
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
                document,
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
                        document,
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
                reviewer_completed = True
                missing_item_ids = adjudication.missing_item_ids
                repaired_output = adjudication.repaired_output
                assessed_attempts = adjudication.assessed_attempts
                grounded_assessed = adjudication.grounded_assessed
                adjudication_warnings = adjudication.validation_warnings
                for key_name, value in adjudication.usage.items():
                    token_usage[key_name] = token_usage.get(key_name, 0) + value
            except AnalysisCancelled:
                raise
            except Exception as exc:
                safe_failure = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    and str(exc).startswith(
                        "Final assessment did not complete:"
                    )
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
            expected_items = rubric_items(profile, module.key, module.items)
            missing = [item for item in expected_items if item in missing_item_ids]
            if missing:
                missing_by_module[module.key] = (
                    "Final assessment omitted: " + ", ".join(missing) + "."
                )
        score = score_findings(
            findings,
            context,
            content_level,
            missing_by_module,
            reviewer_completed,
            profile,
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
            source_quality=_source_quality(document),
        )
        confidence_score = round(
            (
                0.35 * confidence_components.assessment_coverage
                + 0.25 * confidence_components.evidence_module_coverage
                + 0.25 * confidence_components.quote_grounding_rate
                + 0.15 * confidence_components.source_quality
            )
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
                f"{unsupported_attempts} attempted judgments cited no permitted verified evidence "
                "and were excluded from scoring."
            )
        if not reviewer_completed and not reviewer_failure_warning:
            execution_warnings.append(
                "No final adjudicator model was configured; deterministic not-assessed entries "
                "are shown for compatibility."
            )
        summary = _derived_summary(
            findings,
            sum(item.assessed_items for item in score.module_statuses),
            sum(item.expected_items for item in score.module_statuses),
        )
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
