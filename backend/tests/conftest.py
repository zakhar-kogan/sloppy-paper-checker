from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Settings are instantiated while test modules import the application. Pin the
# infrastructure adapters before collection so a developer's deployment .env
# can never redirect the ordinary test suite to shared services.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="sloppy-paper-checker-tests-"))
os.environ["SPC_DATABASE_URL"] = f"sqlite:///{_TEST_ROOT / 'tests.db'}"
os.environ["SPC_DOCUMENT_STORE"] = "filesystem"
os.environ["SPC_DOCUMENT_STORE_PATH"] = str(_TEST_ROOT / "documents")
os.environ["SPC_ANALYSIS_DISPATCHER"] = "inline"
