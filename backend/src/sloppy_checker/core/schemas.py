from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContentLevel(StrEnum):
    METADATA = "metadata"
    ABSTRACT = "abstract"
    FULL_TEXT = "full_text"


class SourceFormat(StrEnum):
    METADATA = "metadata"
    ABSTRACT = "abstract"
    PDF = "pdf"
    JATS = "jats"
    HTML = "html"


class PaperSource(StrictModel):
    kind: Literal["document"] = "document"
    value: str = Field(min_length=1, max_length=2048)


class PaperIdentity(StrictModel):
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    versions: list[str] = Field(default_factory=list)
    fingerprint: str = ""


class ContentCandidate(StrictModel):
    id: str
    format: SourceFormat
    url: HttpUrl | None = None
    version: str | None = None
    license: str | None = None
    provider: str
    content_level: ContentLevel
    rank: int = Field(ge=0)


class ProvenanceRecord(StrictModel):
    provider: str
    available: bool = True
    detail: str | None = None
    accessed_at: datetime


class ResolvedPaper(StrictModel):
    id: UUID
    identity: PaperIdentity
    abstract: str | None = None
    content_level: ContentLevel
    candidates: list[ContentCandidate] = Field(default_factory=list)
    supplements: list[ContentCandidate] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    expires_at: datetime


class ResolveRequest(StrictModel):
    value: str = Field(min_length=1, max_length=2048)


class ResolvedDocumentPreparation(StrictModel):
    failed_candidate_ids: list[str] = Field(default_factory=list, max_length=10)


class BoundingBox(StrictModel):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class DocumentSpan(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    id: str
    text: str = Field(max_length=10000)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    paragraph: str | None = None
    bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def valid_offsets(self) -> DocumentSpan:
        if self.end < self.start:
            raise ValueError("span end must not precede start")
        return self


class DocumentPage(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    number: int = Field(ge=1)
    text: str = Field(max_length=750000)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)


class DocumentSection(StrictModel):
    id: str
    title: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)


class ReferenceEntry(StrictModel):
    id: str
    raw: str = Field(max_length=5000)
    doi: str | None = None


class PaperDocument(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    schema_version: Literal["1.0"] = "1.0"
    identity: PaperIdentity = Field(default_factory=PaperIdentity)
    content_level: ContentLevel
    source_format: SourceFormat
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_name: str
    parser_version: str
    text: str = Field(max_length=8_000_000)
    pages: list[DocumentPage] = Field(default_factory=list, max_length=300)
    spans: list[DocumentSpan] = Field(default_factory=list, max_length=100000)
    sections: list[DocumentSection] = Field(default_factory=list, max_length=1000)
    references: list[ReferenceEntry] = Field(default_factory=list, max_length=5000)
    extraction_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def anchors_within_text(self) -> PaperDocument:
        length = len(self.text)
        anchors = [*self.pages, *self.spans, *self.sections]
        if any(anchor.end > length for anchor in anchors):
            raise ValueError("document anchor exceeds normalized text length")
        return self


class DocumentReceipt(StrictModel):
    id: UUID
    sha256: str
    content_level: ContentLevel
    source_format: SourceFormat


class AnalysisDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class AnalysisState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RubricProfile(StrEnum):
    RANDOMIZED = "randomized_experiment"
    OBSERVATIONAL = "observational_study"
    QUALITATIVE = "qualitative_research"
    SYSTEMATIC_REVIEW = "systematic_review_meta_analysis"
    DIAGNOSTIC = "diagnostic_prediction"
    COMPUTATIONAL = "computational_ml_modeling"
    GENERAL_EMPIRICAL = "general_empirical"
    COMMON_CORE = "common_core"


class RubricGrade(StrEnum):
    NO_CONCERN = "no_concern"
    MINOR_CONCERN = "minor_concern"
    MAJOR_CONCERN = "major_concern"
    CRITICAL_CONCERN = "critical_concern"
    NOT_ASSESSED = "not_assessed"


class FindingSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class PaperSpan(StrictModel):
    section: str | None = None
    page: int | None = Field(default=None, ge=1)
    paragraph: str | None = None
    quote: str = Field(min_length=1, max_length=1200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    bbox: BoundingBox | None = None


class EvidenceSource(StrictModel):
    title: str
    url: HttpUrl
    publisher: str | None = None
    accessed_at: datetime
    identifier: str | None = None


class Finding(StrictModel):
    id: str
    category: str
    rubric_item: str
    title: str
    explanation: str
    severity: FindingSeverity
    grade: RubricGrade
    confidence: Annotated[float, Field(ge=0, le=1)]
    paper_spans: list[PaperSpan] = Field(default_factory=list)
    external_sources: list[EvidenceSource] = Field(default_factory=list)
    affected_conclusions: list[str] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    critic_disposition: Literal["accepted", "revised", "discarded", "unreviewed"] = "accepted"

    @model_validator(mode="after")
    def require_evidence(self) -> Finding:
        if self.severity != FindingSeverity.INFO and not (self.paper_spans or self.external_sources):
            raise ValueError("substantive findings require a paper span or external source")
        return self


class DimensionScore(StrictModel):
    key: str
    label: str
    weight: float
    score: Annotated[float, Field(ge=0, le=100)]
    assessed_items: int = Field(ge=0)
    total_items: int = Field(ge=0)


class Coverage(StrictModel):
    paper: Annotated[float, Field(ge=0, le=1)] = 0
    context: Annotated[float, Field(ge=0, le=1)] = 0
    overall: Annotated[float, Field(ge=0, le=1)] = 0
    available: Annotated[float, Field(ge=0, le=1)] = 0
    full_review: Annotated[float, Field(ge=0, le=1)] = 0
    provisional: bool
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def compatibility_coverage(cls, value: object) -> object:
        if isinstance(value, dict):
            result = dict(value)
            result.setdefault("available", result.get("overall", result.get("paper", 0)))
            result.setdefault("full_review", result.get("paper", 0))
            return result
        return value


class ConfidenceComponents(StrictModel):
    assessment_coverage: Annotated[float, Field(ge=0, le=1)] = 0
    evidence_module_coverage: Annotated[float, Field(ge=0, le=1)] = 0
    quote_grounding_rate: Annotated[float, Field(ge=0, le=1)] = 0


class ContextAssessment(StrictModel):
    retracted: bool = False
    expression_of_concern: bool = False
    corrections: list[str] = Field(default_factory=list)
    venue_signals: list[str] = Field(default_factory=list)
    author_conflict_signals: list[str] = Field(default_factory=list)
    metric_notes: list[str] = Field(default_factory=list)
    record_sources: list[EvidenceSource] = Field(default_factory=list)


class ModuleStatus(StrictModel):
    key: str
    label: str
    state: Literal["completed", "ineligible_at_content_level", "module_failed", "unreviewed"]
    assessed_items: int = Field(ge=0)
    expected_items: int = Field(ge=0)
    limitation: str | None = None


class AnalysisRequest(StrictModel):
    source: PaperSource
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    enabled_checks: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100)
    sequential: bool = False


class AnalysisEvidenceNote(StrictModel):
    module_key: str
    rubric_item: str
    observation: str = Field(max_length=500)
    quotes: list[Annotated[str, Field(max_length=280)]] = Field(default_factory=list, max_length=2)


class AnalysisProgressEvent(StrictModel):
    at: datetime
    kind: Literal["stage", "module"] = "stage"
    key: str | None = None
    label: str
    state: Literal["pending", "running", "completed", "failed", "skipped", "cancelled"]
    progress: int = Field(ge=0, le=100)
    evidence_count: int = Field(default=0, ge=0)
    notes: list[AnalysisEvidenceNote] = Field(default_factory=list)
    detail: str | None = None


class AnalysisStatus(StrictModel):
    id: UUID
    state: AnalysisState
    progress: int = Field(ge=0, le=100)
    stage: str
    created_at: datetime
    updated_at: datetime
    stage_started_at: datetime
    events: list[AnalysisProgressEvent] = Field(default_factory=list)
    error: str | None = None


class AnalysisReport(StrictModel):
    id: UUID
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    scoring_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    identity: PaperIdentity
    profile: RubricProfile
    language: str
    content_level: ContentLevel = ContentLevel.FULL_TEXT
    source_format: SourceFormat = SourceFormat.PDF
    review_score: Annotated[float, Field(ge=0, le=100)] = 0
    composite_score: Annotated[float, Field(ge=0, le=100)]
    uncapped_score: Annotated[float, Field(ge=0, le=100)]
    dimensions: list[DimensionScore]
    coverage: Coverage
    confidence_score: Annotated[float, Field(ge=0, le=100)] = 0
    confidence_components: ConfidenceComponents = Field(default_factory=ConfidenceComponents)
    assessed_item_count: int = Field(default=0, ge=0)
    missing_item_ids: list[str] = Field(default_factory=list)
    failed_evidence_modules: list[str] = Field(default_factory=list)
    repaired_output: bool = False
    execution_warnings: list[str] = Field(default_factory=list)
    evidence_notes: list[AnalysisEvidenceNote] = Field(default_factory=list)
    evidence_verification_rate: Annotated[float, Field(ge=0, le=1)] = 0
    context: ContextAssessment
    module_statuses: list[ModuleStatus] = Field(default_factory=list)
    findings: list[Finding]
    summary: list[str] = Field(default_factory=list)
    banners: list[str]
    limitations: list[str]
    audit_trail: list[dict[str, object]]
    methodology_version: str = "legacy"
    methodology_hash: str = ""
    parser_name: str = "legacy"
    parser_version: str = ""
    provider_profile: str = "legacy"
    provider_protocol: str = "openai-compatible"
    worker_model: str = ""
    reviewer_model: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    paper_sha256: str = ""
    completed_at: datetime

    @model_validator(mode="before")
    @classmethod
    def compatibility_score_alias(cls, value: object) -> object:
        if isinstance(value, dict):
            result = dict(value)
            if "review_score" not in result and "composite_score" in result:
                result["review_score"] = result["composite_score"]
            if "composite_score" not in result and "review_score" in result:
                result["composite_score"] = result["review_score"]
            return result
        return value


class SessionView(StrictModel):
    expires_at: datetime
    hosted_remaining: int | None = Field(default=None, ge=0)
    concurrent_limit: int = Field(ge=1)
