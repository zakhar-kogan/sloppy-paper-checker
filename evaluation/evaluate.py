from __future__ import annotations

import argparse
import json
from pathlib import Path

from sloppy_checker.core.evaluation import evaluate_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one stored analysis report")
    parser.add_argument("report", type=Path)
    parser.add_argument("--case", default="cipriani-2018")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent / "corpus" / "manifest.json",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    case = next((item for item in manifest["cases"] if item["id"] == args.case), None)
    if not case:
        raise SystemExit(f"Unknown evaluation case: {args.case}")
    report = json.loads(args.report.read_text())
    print(json.dumps(evaluate_report(report, case), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
