from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "web" / "public" / "examples"
DB_PATH = ROOT / "paper_checker.db"
DOCUMENT_STORE = ROOT / "data" / "documents"
PARSER = ROOT / "scripts" / "parse_showcase_pdf.mjs"

CORPUS = {
    "dflash-2026": ("arxiv:2602.06036", "Computational"),
    "attention-2017": ("arxiv:1706.03762", "Computational"),
    "episode-2026": ("10.1001/jamapsychiatry.2026.0132", "Randomized"),
    "semaglutide-hiv-2024": ("10.1016/S2213-8587(24)00150-5", "Randomized"),
    "bnt162b2-2020": ("10.1056/NEJMoa2034577", "Randomized"),
    "cipriani-2018": ("10.1016/S0140-6736(17)32802-7", "Systematic review"),
    "opensafely-2020": ("10.1038/s41586-020-2521-4", "Observational"),
    "retinopathy-dls-2017": ("10.1001/jama.2017.18152", "Diagnostic"),
    "hospital-experience-2020": ("10.1177/2374373520942403", "Qualitative"),
    "context-reproducibility-2016": ("10.1073/pnas.1521897113", "General empirical"),
}


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def sanitize_error(error: object) -> str:
    value = re.sub(r"(?i)(api[_ -]?key|authorization|cookie|token)\s*[:=]\s*\S+", r"\1=[redacted]", str(error))
    return value[:500]


def append_ledger(entry: dict[str, Any]) -> None:
    ledger = OUTPUT / "ledger" / "attempts.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def canonical(value: str | None) -> str:
    return (value or "").lower().replace("https://doi.org/", "").replace("doi:", "").replace("arxiv:", "")


def identity_verified(identifier: str, identity: dict[str, Any]) -> bool:
    expected = canonical(identifier)
    return expected in {canonical(identity.get("doi")), canonical(identity.get("arxiv_id"))}


def normalized(value: str) -> str:
    return " ".join(value.casefold().replace("\u00ad", "").split())


def displayed_quotes(report: dict[str, Any]) -> list[str]:
    quotes: list[str] = []
    for finding in report.get("findings", []):
        if finding.get("critic_disposition") == "discarded":
            continue
        quotes.extend(span["quote"] for span in finding.get("paper_spans", []) if span.get("quote"))
    for note in report.get("evidence_notes", []):
        quotes.extend(quote for quote in note.get("quotes", []) if quote)
    return quotes


def audit_report(case_id: str, identifier: str, report: dict[str, Any], source_text: str, source_kind: str) -> dict[str, Any]:
    haystack = normalized(source_text)
    quotes = displayed_quotes(report)
    mismatches = [quote for quote in quotes if normalized(quote) not in haystack]
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "identity_verified": identity_verified(identifier, report["identity"]),
        "source_verified": bool(source_text.strip()),
        "source_kind": source_kind,
        "displayed_quotations_checked": len(quotes),
        "quotation_mismatch_count": len(mismatches),
        "quotation_mismatches": mismatches,
        "warnings_reviewed": False,
        "warnings": report.get("execution_warnings", []),
        "secret_scan": "pending",
        "audited_at": now(),
    }


def metadata_document(resolution: dict[str, Any]) -> dict[str, Any]:
    identity = resolution["identity"]
    parts = [identity.get("title") or ""]
    if identity.get("authors"):
        parts.append("Authors: " + ", ".join(identity["authors"]))
    for label, key in (("Venue", "journal"), ("DOI", "doi"), ("arXiv", "arxiv_id"), ("PMID", "pmid"), ("PMCID", "pmcid")):
        if identity.get(key):
            parts.append(f"{label}: {identity[key]}")
    if resolution.get("abstract"):
        parts.append("Abstract\n" + resolution["abstract"])
    text = "\n\n".join(part for part in parts if part)
    level = "abstract" if resolution.get("abstract") else "metadata"
    return {
        "schema_version": "1.0",
        "identity": identity,
        "content_level": level,
        "source_format": level,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parser_name": "release-metadata",
        "parser_version": "1.0",
        "text": text,
        "pages": [],
        "sections": [],
        "spans": [{"id": "metadata", "text": text, "start": 0, "end": len(text)}],
        "references": [],
        "extraction_warnings": ["No usable full-text candidate; the example is scoped to available metadata or abstract."],
    }


def parse_pdf(pdf_bytes: bytes, identity: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="spc-showcase-") as directory:
        temp = Path(directory)
        pdf_path = temp / "paper.pdf"
        identity_path = temp / "identity.json"
        output_path = temp / "document.json"
        pdf_path.write_bytes(pdf_bytes)
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
        subprocess.run(
            ["node", str(PARSER), str(pdf_path), str(identity_path), str(output_path)],
            cwd=ROOT,
            check=True,
        )
        return json.loads(output_path.read_text(encoding="utf-8"))


def prepare_document(client: httpx.Client, resolution: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    candidates = sorted(resolution.get("candidates", []), key=lambda item: item.get("rank", 999))
    for candidate in candidates:
        try:
            if candidate["format"] == "jats":
                response = client.post(
                    f"/v1/resolutions/{resolution['id']}/documents/{candidate['id']}",
                    json={"failed_candidate_ids": failures},
                )
                response.raise_for_status()
                receipt = response.json()
                document = load_stored_document(receipt["id"])
                delete_stored_document(receipt["id"])
                return document, failures
            if candidate["format"] == "pdf":
                response = client.get(f"/v1/resolutions/{resolution['id']}/artifacts/{candidate['id']}")
                response.raise_for_status()
                return parse_pdf(response.content, resolution["identity"]), failures
        except (httpx.HTTPError, subprocess.CalledProcessError, ValueError, KeyError) as error:
            failures.append(candidate.get("id", "unknown"))
            print(f"{candidate.get('id', 'candidate')} unavailable: {sanitize_error(error)}", flush=True)
    return metadata_document(resolution), failures


def load_stored_document(document_id: str) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute("select object_key from documents where id = ?", (document_id,)).fetchone()
    if not row:
        raise RuntimeError("Prepared document was not found in the local store")
    path = (DOCUMENT_STORE / row[0]).resolve()
    if path.parent != DOCUMENT_STORE.resolve():
        raise RuntimeError("Document path escaped the configured local store")
    return json.loads(path.read_text(encoding="utf-8"))


def delete_stored_document(document_id: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute("select object_key from documents where id = ?", (document_id,)).fetchone()
        if not row:
            return
        path = (DOCUMENT_STORE / row[0]).resolve()
        if path.parent != DOCUMENT_STORE.resolve():
            raise RuntimeError("Document cleanup path escaped the configured local store")
        connection.execute("delete from documents where id = ?", (document_id,))
        connection.commit()
    path.unlink(missing_ok=True)


def create_document(client: httpx.Client, document: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/documents", json=document)
    if response.status_code == 422:
        raise RuntimeError(f"Document validation failed: {response.text[:1000]}")
    response.raise_for_status()
    return response.json()


def run_analysis(client: httpx.Client, document: dict[str, Any]) -> tuple[dict[str, Any], float]:
    receipt = create_document(client, document)
    started = time.monotonic()
    response = client.post("/v1/analyses", json={"source": {"kind": "document", "value": receipt["id"]}})
    response.raise_for_status()
    status = response.json()
    while status["state"] not in {"completed", "failed", "cancelled"}:
        time.sleep(1.5)
        response = client.get(f"/v1/analyses/{status['id']}")
        response.raise_for_status()
        status = response.json()
        print(f"{status['id']} {status['progress']:>3}% {status['stage']}", flush=True)
    latency = time.monotonic() - started
    if status["state"] != "completed":
        raise RuntimeError(status.get("error") or f"Analysis ended as {status['state']}")
    response = client.get(f"/v1/analyses/{status['id']}/report")
    response.raise_for_status()
    return response.json(), latency


def load_reused_report(analysis_id: str) -> tuple[dict[str, Any], float]:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "select report, (julianday(updated_at) - julianday(created_at)) * 86400 from analyses where id = ? and state = 'completed'",
            (analysis_id,),
        ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"Completed analysis {analysis_id} was not found")
    return json.loads(row[0]), round(float(row[1] or 0), 3)


def write_artifacts(case_id: str, identifier: str, report: dict[str, Any], source_text: str, source_kind: str) -> None:
    reports = OUTPUT / "reports"
    audits = OUTPUT / "audits"
    reports.mkdir(parents=True, exist_ok=True)
    audits.mkdir(parents=True, exist_ok=True)
    (reports / f"{case_id}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit = audit_report(case_id, identifier, report, source_text, source_kind)
    (audits / f"{case_id}.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate or export one fixed showcase report")
    parser.add_argument("case_id", choices=CORPUS)
    parser.add_argument("--api-base", default="http://127.0.0.1:8787")
    parser.add_argument("--reuse-analysis")
    args = parser.parse_args()
    identifier, intended_profile = CORPUS[args.case_id]
    started_at = now()
    started = time.monotonic()
    report: dict[str, Any] | None = None
    document: dict[str, Any] | None = None
    error: object | None = None
    latency = 0.0
    try:
        with httpx.Client(base_url=args.api_base, timeout=60.0, follow_redirects=True) as client:
            client.post("/v1/session").raise_for_status()
            resolution_response = client.post("/v1/resolve", json={"value": identifier})
            resolution_response.raise_for_status()
            resolution = resolution_response.json()
            document, source_failures = prepare_document(client, resolution)
            document["extraction_warnings"] = [
                *document.get("extraction_warnings", []),
                *[f"Source candidate {candidate_id} could not be prepared during release preflight." for candidate_id in source_failures],
            ]
            if args.reuse_analysis:
                report, latency = load_reused_report(args.reuse_analysis)
            else:
                report, latency = run_analysis(client, document)
        write_artifacts(args.case_id, identifier, report, document["text"], document["source_format"])
    except Exception as caught:  # release ledger must capture all terminal attempts
        error = caught
        raise
    finally:
        append_ledger({
            "case_id": args.case_id,
            "attempted_at": started_at,
            "git_commit": git_commit(),
            "result_state": "completed" if report is not None and error is None else "failed",
            "intended_profile": intended_profile,
            "content_level": report.get("content_level") if report else document.get("content_level") if document else None,
            "source_format": report.get("source_format") if report else document.get("source_format") if document else None,
            "methodology_hash": report.get("methodology_hash") if report else None,
            "paper_sha256": report.get("paper_sha256") if report else document.get("sha256") if document else None,
            "parser": f"{report.get('parser_name')} {report.get('parser_version')}" if report else f"{document.get('parser_name')} {document.get('parser_version')}" if document else None,
            "models": {"worker": report.get("worker_model"), "reviewer": report.get("reviewer_model")} if report else {},
            "token_usage": report.get("token_usage", {}) if report else {},
            "latency_seconds": round(latency or (time.monotonic() - started), 3),
            "warnings": report.get("execution_warnings", []) if report else document.get("extraction_warnings", []) if document else [],
            "sanitized_error": sanitize_error(error) if error else None,
            "reused_analysis_id": args.reuse_analysis,
        })


if __name__ == "__main__":
    main()
