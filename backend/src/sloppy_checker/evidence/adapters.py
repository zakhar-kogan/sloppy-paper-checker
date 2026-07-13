from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from sloppy_checker.core.ingest import normalize_doi


@dataclass
class AdapterResult:
    source: str
    available: bool
    data: dict[str, Any]
    limitation: str | None = None


class EvidenceClient:
    def __init__(self, timeout: float = 12.0):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers={"User-Agent": "sloppy-paper-checker/0.1 (mailto:operator@example.invalid)"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, source: str, url: str, params: dict | None = None) -> AdapterResult:
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return AdapterResult(source, True, response.json())
        except (httpx.HTTPError, ValueError) as exc:
            return AdapterResult(source, False, {}, f"{source} unavailable: {type(exc).__name__}")

    async def crossref(self, doi: str) -> AdapterResult:
        doi = normalize_doi(doi)
        result = await self._get("Crossref", f"https://api.crossref.org/works/{doi}")
        if result.available:
            result.data = result.data.get("message", {})
        return result

    async def openalex(self, doi: str) -> AdapterResult:
        return await self._get("OpenAlex", "https://api.openalex.org/works/https://doi.org/" + normalize_doi(doi))

    async def datacite(self, doi: str) -> AdapterResult:
        return await self._get("DataCite", "https://api.datacite.org/dois/" + normalize_doi(doi))

    async def doaj(self, issn: str) -> AdapterResult:
        return await self._get("DOAJ", f"https://doaj.org/api/search/journals/issn:{issn}")

