from __future__ import annotations

import json
import re

from sloppy_checker.core.schemas import AnalysisReport

from finalize_showcase import CORPUS, EXAMPLES, ROOT, SECRET_PATTERNS, concern_count


def main() -> None:
    manifest_path = EXAMPLES / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_ids = [case_id for case_id, _ in CORPUS]
    actual_ids = [example["id"] for example in manifest["examples"]]
    assert manifest["schema_version"] == "1.0"
    assert actual_ids == expected_ids, f"Expected fixed corpus order {expected_ids}, got {actual_ids}"
    assert len(actual_ids) == len(set(actual_ids)) == 10

    completed_ledger_cases: set[str] = set()
    ledger_path = EXAMPLES / "ledger" / "attempts.jsonl"
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        required = {
            "case_id", "attempted_at", "git_commit", "result_state", "content_level",
            "source_format", "methodology_hash", "paper_sha256", "parser", "models",
            "token_usage", "latency_seconds", "warnings", "sanitized_error",
        }
        assert required <= entry.keys(), f"Ledger entry is incomplete: {entry.get('case_id')}"
        if entry["result_state"] == "completed":
            completed_ledger_cases.add(entry["case_id"])
    assert completed_ledger_cases == set(expected_ids)

    for example in manifest["examples"]:
        report_path = (EXAMPLES / example["report"]).resolve()
        audit_path = (EXAMPLES / example["audit"]).resolve()
        assert report_path.parent == (EXAMPLES / "reports").resolve()
        assert audit_path.parent == (EXAMPLES / "audits").resolve()
        assert report_path.is_file() and audit_path.is_file()
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        report = AnalysisReport.model_validate(report_data)
        assert report.schema_version == report.scoring_version == "1.3"
        assert report.identity.title == example["title"]
        assert report.profile.value == example["profile"]
        assert report.content_level.value == example["content_level"]
        assert report.coverage.full_review == example["coverage"]
        assert concern_count(report_data) == example["concern_count"]
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        assert audit["case_id"] == example["id"]
        assert audit["identity_verified"] and audit["source_verified"]
        assert audit["quotation_mismatch_count"] == 0
        assert audit["warnings_reviewed"]
        assert audit["secret_scan"] == "passed"

    for path in EXAMPLES.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert not any(pattern.search(content) for pattern in SECRET_PATTERNS), f"Potential secret in {path.relative_to(ROOT)}"
            assert not re.search(r"(?i)BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", content)
    print("Validated 10 AnalysisReport v1.3 files, audits, manifest references, ledger coverage, and secret scan.")


if __name__ == "__main__":
    main()
