from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaperSourceKind(StrEnum):
    DOI = "doi"
    URL = "url"
    UPLOAD = "upload"


class PaperSource(StrictModel):
    kind: PaperSourceKind
    value: str = Field(min_length=1, max_length=2048)


class PaperIdentity(StrictModel):
    doi: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    published_at: str | None = None
    fingerprint: str


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
    quote: str = Field(min_length=1, max_length=1200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


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
    critic_disposition: Literal["accepted", "revised", "discarded"] = "accepted"

    @model_validator(mode="after")
    def require_evidence(self) -> Finding:
        if self.severity != FindingSeverity.INFO and not (
            self.paper_spans or self.external_sources
        ):
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
    paper: Annotated[float, Field(ge=0, le=1)]
    context: Annotated[float, Field(ge=0, le=1)]
    overall: Annotated[float, Field(ge=0, le=1)]
    provisional: bool
    limitations: list[str] = Field(default_factory=list)


class ContextAssessment(StrictModel):
    retracted: bool = False
    expression_of_concern: bool = False
    corrections: list[str] = Field(default_factory=list)
    venue_signals: list[str] = Field(default_factory=list)
    author_conflict_signals: list[str] = Field(default_factory=list)
    metric_notes: list[str] = Field(default_factory=list)


class AnalysisRequest(StrictModel):
    source: PaperSource
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    enabled_checks: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100)
    sequential: bool = False


class AnalysisStatus(StrictModel):
    id: UUID
    state: AnalysisState
    progress: int = Field(ge=0, le=100)
    stage: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class AnalysisReport(StrictModel):
    id: UUID
    schema_version: Literal["1.0"] = "1.0"
    scoring_version: Literal["1.0"] = "1.0"
    identity: PaperIdentity
    profile: RubricProfile
    language: str
    composite_score: Annotated[float, Field(ge=0, le=100)]
    uncapped_score: Annotated[float, Field(ge=0, le=100)]
    dimensions: list[DimensionScore]
    coverage: Coverage
    context: ContextAssessment
    findings: list[Finding]
    banners: list[str]
    limitations: list[str]
    audit_trail: list[dict[str, object]]
    completed_at: datetime


class UploadReceipt(StrictModel):
    id: UUID
    sha256: str
    bytes: int


class ProviderSettingsInput(StrictModel):
    base_url: HttpUrl = "https://api.tokenfactory.nebius.com/v1/"
    api_key: str | None = Field(default=None, min_length=8, max_length=4096)
    clear_api_key: bool = False
    planner_model: str = ""
    worker_model: str = ""
    critic_model: str = ""
    default_depth: AnalysisDepth = AnalysisDepth.STANDARD
    max_concurrency: int = Field(default=5, ge=1, le=16)
    sequential_mode: bool = False
    max_cost_usd: float = Field(default=2.0, gt=0, le=100)
    retention_days: int = Field(default=30, ge=0, le=3650)


class ProviderSettingsView(StrictModel):
    base_url: HttpUrl
    has_api_key: bool
    api_key_source: Literal["encrypted", "environment", "missing"]
    planner_model: str
    worker_model: str
    critic_model: str
    default_depth: AnalysisDepth
    max_concurrency: int
    sequential_mode: bool
    max_cost_usd: float
    retention_days: int

