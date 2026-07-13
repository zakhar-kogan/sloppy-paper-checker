from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from pypdf import PdfReader

from .config import AppSettings

DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)


def normalize_doi(value: str) -> str:
    match = DOI_RE.search(value.strip())
    if not match:
        raise ValueError("A valid DOI was not found")
    return match.group(1).rstrip(".,;)]").lower()


async def save_pdf(upload: UploadFile, settings: AppSettings) -> tuple[str, int, Path]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    target = settings.upload_dir / f"{uuid4()}.pdf"
    digest = hashlib.sha256()
    total = 0
    first = b""
    try:
        with target.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                if not first:
                    first = chunk[:8]
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ValueError("PDF exceeds the configured upload limit")
                digest.update(chunk)
                output.write(chunk)
        if not first.startswith(b"%PDF-"):
            raise ValueError("Upload is not a PDF")
        return digest.hexdigest(), total, target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def extract_pdf_text(path: Path, max_pages: int = 300) -> tuple[str, int]:
    data = path.read_bytes()
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported")
    if len(reader.pages) > max_pages:
        raise ValueError("PDF has too many pages")
    pages = []
    for index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {index}]\n{text}")
    return "\n\n".join(pages), len(reader.pages)


def fingerprint_text(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()
