from __future__ import annotations

import hashlib
import json

from .config import AppSettings
from .methodology import load_methodology
from .schemas import AnalysisReport, PaperDocument, RubricProfile

ANALYSIS_COMPATIBILITY_VERSION = "1"


def _digest(parts: dict[str, str]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def document_compatibility_hash(
    document: PaperDocument,
    profile: RubricProfile,
    settings: AppSettings,
) -> str:
    methodology = load_methodology()
    return _digest(
        {
            "compatibility_version": ANALYSIS_COMPATIBILITY_VERSION,
            "paper_sha256": document.sha256,
            "content_level": document.content_level.value,
            "source_format": document.source_format.value,
            "profile": profile.value,
            "scoring_version": "1.3",
            "methodology_hash": methodology.bundle_hash,
            "parser_name": document.parser_name,
            "parser_version": document.parser_version,
            "provider_profile": settings.provider_profile,
            "worker_model": settings.configured_provider_worker_model,
            "reviewer_model": settings.configured_provider_reviewer_model,
        }
    )


def report_compatibility_hash(report: AnalysisReport) -> str:
    return _digest(
        {
            "compatibility_version": ANALYSIS_COMPATIBILITY_VERSION,
            "paper_sha256": report.paper_sha256,
            "content_level": report.content_level.value,
            "source_format": report.source_format.value,
            "profile": report.profile.value,
            "scoring_version": report.scoring_version,
            "methodology_hash": report.methodology_hash,
            "parser_name": report.parser_name,
            "parser_version": report.parser_version,
            "provider_profile": report.provider_profile,
            "worker_model": report.worker_model,
            "reviewer_model": report.reviewer_model,
        }
    )


def has_public_identifier(report: AnalysisReport) -> bool:
    identity = report.identity
    return any((identity.doi, identity.arxiv_id, identity.pmid, identity.pmcid))
