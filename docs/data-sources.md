# Resolution and evidence sources

The resolver recognizes DOI strings/URLs, arXiv IDs/pages, PMID, PMCID, direct PDF URLs, and scholarly pages. It combines Crossref metadata, Unpaywall locations, the arXiv API, NCBI identifier/PubMed services, and PMC JATS.

Candidates are ranked published PDF, accepted PDF, submitted PDF, PMC JATS, then abstract/metadata fallback. Each candidate records provider, version, license, format, content level, URL, and rank; the public API exposes only an opaque candidate ID to relay/download routes. The JATS normalizer includes the abstract, body, affiliations, acknowledgments, funding, conflicts, back-matter sections, and figure/table captions. Linked supplement descriptions are recorded, but supplement file contents are not analyzed and are disclosed as unavailable.

Publication updates from current-paper Crossref metadata can appear in the report. Missing source data is a limitation, never adverse evidence. This release does not retrieve cited-paper full texts, investigate citation networks, construct author-history networks, search social media, or conduct a comprehensive literature-bias review.

Cloudflare Markdown Conversion settings are experimental and disabled by default. Markdown is not a `PaperDocument.source_format`, so conversion output cannot become canonical evidence or silently replace PDF.js/JATS spans. Any future use must remap quotes to the canonical coordinates first.
