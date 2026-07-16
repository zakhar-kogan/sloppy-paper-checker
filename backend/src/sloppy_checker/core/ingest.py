from __future__ import annotations

import hashlib
import re

DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)


def normalize_doi(value: str) -> str:
    match = DOI_RE.search(value.strip())
    if not match:
        raise ValueError("A valid DOI was not found")
    return match.group(1).rstrip(".,;)]").lower()


def fingerprint_text(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()
