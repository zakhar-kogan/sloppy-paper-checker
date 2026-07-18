from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_committed_showcase_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    subprocess.run(  # noqa: S603 - fixed repository script and current interpreter
        [sys.executable, str(root / "scripts" / "validate_showcase.py")],
        cwd=root,
        check=True,
    )
