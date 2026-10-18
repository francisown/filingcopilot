# Verification notes — 2026-10-18

## Bundled filing sources

Six Apple, Microsoft and NVIDIA filings were downloaded again from their exact SEC
URLs into an empty cache, along with submissions and CompanyFacts responses. Full
re-extraction reproduced all 132 existing facts and 2,728 passages.

The seed keeps its original IDs, order, fields and observations. Only the six
validated filing-byte bindings changed; additional extracted tables were excluded
to keep retrieval scope and data size stable. Existing databases are not migrated
by this update. Later byte conflicts still produce an integrity error.

- [Download receipt](validation/demo-source-acquisition-2026-10-18.json)
- [Re-extraction and preservation receipt](validation/demo-source-rebuild-2026-10-18.json)

`scripts/rebuild_demo_sources.py` repeats the extraction from an explicit SEC cache.
It rejects changed or missing observations and writes new output files for review.

Ten cases compared old and repaired seeds in fresh databases, using the same current
application code and complete raw cache. The old seed was withheld by integrity
checks. The repaired seed answered all ten; source audits were consistent, with no
missing sources and no model calls. This comparison isolates the seed change.

## Verification code

The public `verify_submission` interface is unchanged. Its private methods share
scope and source-cache checks, then handle numeric, prose, analysis and external
claims. Semantic review runs after those checks; numeric completeness runs last.

System-prompt assembly and observed financial completion moved out of the agent's
main run function. The latter now has 601 lines, down from 743, and remains a
candidate for further refactoring. Existing behavior tests cover both paths.

## Review calibration

The blind-sample exporter now checks model identities in the run trace. Reviewers
must differ from every model that authored a draft, including fallback authors.
Unknown independence and missing model history are excluded from the independent
stratum; the claims remain available for ordinary human assessment.

In the optimized arm of the 2026-10-18 paired run, 14 claims across four answers no longer qualify
as independently reviewed. The eligible set contains 253 claims across 74 answers.
These counts describe review identity, not claim correctness.

A 20-claim historical worksheet is available for human labeling. Labels are blank
and the scoring key is separate. Complete-answer independent human evaluation
remains unfinished.

## Checks

The [validation receipt](validation/reliability-followup-2026-10-18.json) records
1,624 passing tests, 33 focused export/worksheet checks, 21 historical package
replays and both wheel checks. No paid model calls were used in this cycle.
These local checks do not include hosted cross-platform CI or production deployment.
