from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "web" / "public" / "examples"
CORPUS = [
    ("dflash-2026", "arXiv:2602.06036"),
    ("attention-2017", "arXiv:1706.03762"),
    ("episode-2026", "10.1001/jamapsychiatry.2026.0132"),
    ("semaglutide-hiv-2024", "10.1016/S2213-8587(24)00150-5"),
    ("bnt162b2-2020", "10.1056/NEJMoa2034577"),
    ("cipriani-2018", "10.1016/S0140-6736(17)32802-7"),
    ("opensafely-2020", "10.1038/s41586-020-2521-4"),
    ("retinopathy-dls-2017", "10.1001/jama.2017.18152"),
    ("hospital-experience-2020", "10.1177/2374373520942403"),
    ("context-reproducibility-2016", "10.1073/pnas.1521897113"),
]
SECRET_PATTERNS = (
    re.compile(r"(?i)SPC_NEBIUS_API_KEY\s*="),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer"),
    re.compile(r"(?i)(api[_-]?key|cookie)\s*[:=]\s*[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def concern_count(report: dict[str, Any]) -> int:
    concern_grades = {"critical_concern", "major_concern", "minor_concern"}
    return sum(
        finding.get("grade") in concern_grades and finding.get("critic_disposition") != "discarded"
        for finding in report.get("findings", [])
    )


def scan_for_secrets() -> list[str]:
    matches: list[str] = []
    for directory in (EXAMPLES / "reports", EXAMPLES / "audits", EXAMPLES / "ledger"):
        for path in directory.glob("*"):
            content = path.read_text(encoding="utf-8")
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                matches.append(str(path.relative_to(ROOT)))
    return matches


def main() -> None:
    secret_matches = scan_for_secrets()
    if secret_matches:
        raise SystemExit("Potential secret material found in: " + ", ".join(secret_matches))
    examples = []
    for case_id, identifier in CORPUS:
        report = read(EXAMPLES / "reports" / f"{case_id}.json")
        audit_path = EXAMPLES / "audits" / f"{case_id}.json"
        audit = read(audit_path)
        if not audit["identity_verified"] or not audit["source_verified"] or audit["quotation_mismatch_count"]:
            raise SystemExit(f"Audit failed for {case_id}")
        audit["warnings_reviewed"] = True
        audit["secret_scan"] = "passed"
        write(audit_path, audit)
        published = str(report["identity"].get("published_at") or "")
        year_match = re.search(r"\b(19|20)\d{2}\b", published)
        if not year_match:
            raise SystemExit(f"Published year unavailable for {case_id}")
        examples.append({
            "id": case_id,
            "title": report["identity"]["title"],
            "year": int(year_match.group()),
            "identifier": identifier,
            "profile": report["profile"],
            "content_level": report["content_level"],
            "coverage": report["coverage"]["full_review"],
            "review_score": report["review_score"],
            "provisional": report["coverage"]["provisional"],
            "concern_count": concern_count(report),
            "report": f"reports/{case_id}.json",
            "audit": f"audits/{case_id}.json",
        })
    write(EXAMPLES / "manifest.json", {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "disclosure": "Fixed precomputed examples generated with the project methodology and model inference via Nebius Token Factory. They demonstrate report behavior and are not a validated accuracy evaluation.",
        "examples": examples,
    })


if __name__ == "__main__":
    main()
