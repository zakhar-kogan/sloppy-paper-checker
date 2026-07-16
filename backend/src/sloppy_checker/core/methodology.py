from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import ContentLevel


class MethodologyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModuleDefinition(MethodologyModel):
    key: str
    label: str
    weight: float = Field(gt=0)
    minimum_content_level: ContentLevel
    required_sections: list[str]
    keywords: list[str]
    items: list[str]


class RoutingDefinition(MethodologyModel):
    target_chunk_chars: int = Field(ge=500, le=20000)
    overlap_chars: int = Field(ge=0, le=4000)
    max_chunks_per_module: int = Field(ge=1, le=30)
    max_module_chars: int = Field(ge=2000, le=200000)
    reviewer_max_chars: int = Field(ge=10000, le=500000)


class EvidenceDefinition(MethodologyModel):
    substantive_requires_span_or_source: bool
    reviewer_may_introduce_evidence: bool
    retrieve_cited_full_text: bool


class MethodologyDefinition(MethodologyModel):
    version: str
    name: str
    worker_prompt: str
    reviewer_prompt: str
    grade_scores: dict[str, float]
    profiles: list[str]
    routing: RoutingDefinition
    evidence: EvidenceDefinition
    external_adapters: list[str]
    modules: list[ModuleDefinition]
    failure_policy: dict[str, str]
    score: dict[str, float | bool]

    @model_validator(mode="after")
    def validate_bundle_contract(self) -> MethodologyDefinition:
        if set(self.grade_scores) != {
            "no_concern",
            "minor_concern",
            "major_concern",
            "critical_concern",
        }:
            raise ValueError("methodology must define the four supported grade scores")
        if any(not 0 <= value <= 100 for value in self.grade_scores.values()):
            raise ValueError("grade scores must be between 0 and 100")
        if abs(sum(module.weight for module in self.modules) - 100) > 0.001:
            raise ValueError("methodology module weights must sum to 100")
        keys = [module.key for module in self.modules]
        if len(keys) != len(set(keys)):
            raise ValueError("methodology module keys must be unique")
        if self.routing.overlap_chars >= self.routing.target_chunk_chars:
            raise ValueError("chunk overlap must be smaller than the target chunk")
        if (
            not self.evidence.substantive_requires_span_or_source
            or not self.evidence.reviewer_may_introduce_evidence
            or self.evidence.retrieve_cited_full_text
        ):
            raise ValueError("methodology evidence policy conflicts with the supported trust boundary")
        return self


@dataclass(frozen=True)
class MethodologyBundle:
    definition: MethodologyDefinition
    worker_prompt: str
    reviewer_prompt: str
    bundle_hash: str


def _root() -> Path:
    return Path(__file__).resolve().parents[1] / "methodologies"


@lru_cache
def load_methodology() -> MethodologyBundle:
    manifest_path = _root() / "scientific-paper-review-v1.yaml"
    raw = manifest_path.read_bytes()
    definition = MethodologyDefinition.model_validate(yaml.safe_load(raw))
    worker_path = (_root() / definition.worker_prompt).resolve()
    reviewer_path = (_root() / definition.reviewer_prompt).resolve()
    root = _root().resolve()
    if root not in worker_path.parents or root not in reviewer_path.parents:
        raise ValueError("methodology prompt references must stay inside the bundle")
    worker = worker_path.read_text()
    reviewer = reviewer_path.read_text()
    digest = hashlib.sha256(raw + worker.encode() + reviewer.encode()).hexdigest()
    return MethodologyBundle(definition, worker, reviewer, digest)


def content_allows(actual: ContentLevel, minimum: ContentLevel) -> bool:
    order = {ContentLevel.METADATA: 0, ContentLevel.ABSTRACT: 1, ContentLevel.FULL_TEXT: 2}
    return order[actual] >= order[minimum]
