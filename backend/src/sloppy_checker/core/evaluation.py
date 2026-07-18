from __future__ import annotations

from datetime import datetime
from typing import Any


def _contains_any(value: str, terms: list[str]) -> bool:
    normalized = value.casefold()
    return any(term.casefold() in normalized for term in terms)


def evaluate_report(report: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    notes = list(report.get("evidence_notes") or [])
    findings = list(report.get("findings") or [])
    evidence_expectations = list(case.get("expected_evidence") or [])
    finding_expectations = list(case.get("expected_findings") or [])

    evidence_hits: dict[str, bool] = {}
    false_absences = 0
    for expectation in evidence_expectations:
        matching = [
            note
            for note in notes
            if not expectation.get("module_key")
            or note.get("module_key") == expectation["module_key"]
            if not expectation.get("rubric_item")
            or note.get("rubric_item") == expectation["rubric_item"]
        ]
        observed = [note for note in matching if note.get("evidence_state") == "observed"]
        searchable = " ".join(
            str(part)
            for note in observed
            for part in [note.get("observation", ""), *(note.get("quotes") or [])]
        )
        hit = _contains_any(searchable, list(expectation.get("terms") or []))
        evidence_hits[str(expectation["id"])] = hit
        if not hit and any(note.get("evidence_state") == "not_found" for note in matching):
            false_absences += 1

    finding_hits: dict[str, bool] = {}
    for expectation in finding_expectations:
        allowed = set(expectation.get("grades") or [])
        matching = [
            finding
            for finding in findings
            if finding.get("rubric_item") == expectation.get("rubric_item")
        ]
        finding_hits[str(expectation["id"])] = any(
            not allowed or finding.get("grade") in allowed for finding in matching
        )

    assessed = [item for item in findings if item.get("grade") != "not_assessed"]
    grounded = [
        item
        for item in assessed
        if item.get("paper_spans")
    ]
    unsupported = len(assessed) - len(grounded)
    forbidden_items = set(case.get("forbidden_assessed_items") or [])
    forbidden_assessed = [
        item for item in assessed if item.get("rubric_item") in forbidden_items
    ]
    warnings = list(report.get("execution_warnings") or [])
    timestamps = []
    for event in report.get("audit_trail") or []:
        try:
            timestamps.append(datetime.fromisoformat(str(event["at"])))
        except (KeyError, TypeError, ValueError):
            continue
    latency_seconds = (
        round((max(timestamps) - min(timestamps)).total_seconds(), 3)
        if len(timestamps) >= 2
        else None
    )
    return {
        "case_id": case.get("id"),
        "evidence_hits": evidence_hits,
        "finding_hits": finding_hits,
        "profile_match": not case.get("profile") or report.get("profile") == case["profile"],
        "source_format_match": not case.get("source_format")
        or report.get("source_format") == case["source_format"],
        "expected_evidence_recall": round(
            sum(evidence_hits.values()) / len(evidence_hits), 3
        )
        if evidence_hits
        else 1.0,
        "expected_finding_recall": round(
            sum(finding_hits.values()) / len(finding_hits), 3
        )
        if finding_hits
        else 1.0,
        "false_absence_rate": round(false_absences / len(evidence_hits), 3)
        if evidence_hits
        else 0.0,
        "grounding_rate": round(len(grounded) / len(assessed), 3) if assessed else 0.0,
        "unsupported_finding_rate": round(unsupported / len(assessed), 3)
        if assessed
        else 0.0,
        "forbidden_assessed_items": [
            str(item.get("rubric_item")) for item in forbidden_assessed
        ],
        "coverage": float((report.get("coverage") or {}).get("full_review", 0)),
        "reviewer_repaired": bool(report.get("repaired_output")),
        "reviewer_timed_out": any("reviewer" in item.casefold() and "timeout" in item.casefold() for item in warnings),
        "latency_seconds": latency_seconds,
        "token_usage": dict(report.get("token_usage") or {}),
    }
