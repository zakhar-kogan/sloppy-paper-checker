# Evidence sources and metric semantics

Default adapters use Crossref (including its Retraction Watch relationship metadata), DataCite, OpenAlex, OpenCitations-compatible records, and DOAJ. Future official-source adapters cover ORCID/ROR, ClinicalTrials.gov, NIH RePORTER, CMS Open Payments, registrations, and paper disclosures. Every source failure is a coverage limitation rather than adverse evidence.

- **Retractions and updates:** Crossref relationships are checked and surfaced with provenance. A correction is not a retraction.
- **Venue transparency:** DOAJ and sourced COPE/OASPA/Think.Check.Submit signals can describe specific practices. Absence is neutral.
- **Citation and standing metrics:** OpenAlex/OpenCitations values are labeled by their source and field-normalization method. They are never called “Journal Impact Factor.”
- **Journal Impact Factor:** exact JIF is exposed only by an optional, operator-licensed Clarivate Journals API connector.
- **Authors and conflicts:** identity matching requires stable identifiers or corroborating affiliation/name evidence. Commercial affiliations are reported only when relevant and sourced. Coverage is jurisdiction- and database-dependent.
- **Post-publication discussion:** a future PubPeer connector requires an operator API key; comments are displayed as discussion, not verified misconduct.

Cabells, Clarivate, scite-style services, and similar commercial products require the operator’s own license. No open authoritative “predatory journal API” is treated as ground truth.

