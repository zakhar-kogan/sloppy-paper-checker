# Evaluation corpus

This directory stores metadata and bounded synthetic regression reports, not redistributed copyrighted papers. Every tracked case has executable expectations; placeholder taxonomy entries do not belong in the corpus.

Each case records expected paper type, status banners, assessable dimensions, and source-backed expected findings. Evaluation reports code coverage and **unsupported-finding rate** separately; every substantive output must resolve to a paper span or external source.

Security cases cover prompt injection in paper text, malformed/malicious PDFs, SSRF, oversized uploads, rendered XSS strings, identity collisions, and stigmatizing/defamatory wording.

`evaluation/evaluate.py` scores a stored report against a case without storing the
paper itself. CI uses synthetic report fixtures for deterministic contract checks;
live Token Factory runs are separate, non-blocking smoke tests. Metrics include expected-evidence
and expected-finding recall, false absences, grounding, unsupported findings,
coverage, reviewer repair/timeout state, and token usage.
