# Resolution and evidence sources

The resolver recognizes DOI strings/URLs, arXiv IDs/pages, PMID, PMCID, direct PDF URLs, and scholarly pages. It combines Crossref metadata, Unpaywall locations, the arXiv API, NCBI identifier/PubMed services, PMC JATS, and PMC article HTML.

Candidates are ranked published PDF, accepted PDF, published PMC JATS, published PMC HTML, submitted PDF, then abstract/metadata fallback. A selected source is attempted first, but blocked or invalid full text falls through automatically with one deduplicated provenance warning per public provider/format/version label. Each candidate records provider, version, license, format, content level, URL, and rank; the public API exposes only an opaque candidate ID to relay/download routes. The JATS normalizer includes the abstract, body, affiliations, acknowledgments, funding, conflicts, back-matter sections, and figure/table captions. The PMC HTML candidate extracts the article element into stable paragraph anchors and is ranked after JATS. Linked supplement descriptions are recorded, but supplement file contents are not analyzed and are disclosed as unavailable.

arXiv recognition is limited to bare arXiv identifiers and `arxiv.org` URLs. Lancet/Elsevier `S…` and `PIIS…` paths are normalized to DOI before publisher access. If an opaque publisher page is blocked before a canonical identifier is recovered, the resolver asks for a DOI, PMID, or PMCID instead of guessing or scraping another provider.

Publication updates from current-paper Crossref metadata can appear in the report. Missing source data is a limitation, never adverse evidence. This release does not retrieve cited-paper full texts, investigate citation networks, construct author-history networks, search social media, or conduct a comprehensive literature-bias review.

Cloudflare Markdown Conversion settings are experimental and disabled by default. Markdown is not a `PaperDocument.source_format`, so conversion output cannot become canonical evidence or silently replace PDF.js/JATS spans. Any future use must remap quotes to the canonical coordinates first.
