# FilingCopilot research package protocol

`filingcopilot.research-package/v1` is a self-contained JSON snapshot for
replaying the deterministic part of a research answer. It is intended for code
review, archival hand-off, and offline checking; it is not a signature or a
claim that a narrative judgement is true.

## Output language

`research.language` optionally records `zh` or `en`; absence means the legacy
Chinese renderer. New English packages require a verifier that supports this
field and the English numeric renderer. Checksums bind the original stored text,
and numeric verification renders in the recorded language. Source evidence is
never translated in place. Existing Chinese packages remain readable without
adding the field or rewriting their bytes.

## Required top-level fields

- `schema_version`: exactly `filingcopilot.research-package/v1`.
- `producer_version`: the FilingCopilot version that created the package.
- `domain_origin`: `research_time` for new runs, or
  `export_time_legacy` for old runs without a frozen domain snapshot.
- `focus_ticker`: the requested security, when the run has one.
- `domain`: the complete metric, ratio, company, and industry configuration
  snapshot used by the run.
- `research`: the `ResearchAnswer`, including its knowledge date, claims,
  gaps, evidence, trace, resource usage, and research status.
- `manifest`: `{ "algorithm": "sha256", "payload_sha256": ... }` where the
  digest is SHA-256 over all other top-level fields using sorted keys and the
  compact UTF-8 JSON representation.

## Evidence and source identity

Evidence is recursive. Financial facts and statement rows retain the filing
identity, filing date, period end, unit/currency, source URL, and XBRL or table
locator. Passage citations retain exact text and a locator; table passages keep
their header, unit, period column, and row together in `table_context`. Market
and supplier valuation evidence retain the raw-response SHA-256 and retrieval
time. External updates retain source type, publication/availability times,
evidence level, URL, and content SHA-256. A calculation stores its formula and
input IDs; it is never trusted as a free-floating number.

New market snapshots also retain an optional `cross_check_source`: a compact
secondary-source reference containing the raw SHA-256, URL, retrieval time,
ticker, period, knowledge date and stale flag. It does not duplicate a second
price curve. A source conflict compares the two providers' **same-date Close
observations**; the latest quote can have a different time and value and stays
separate. Different tickers, currencies or latest trading dates produce a
source warning instead of an invented numeric disagreement.

## Offline verification

The standalone verifier needs only a package:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package artifacts\package.json --output artifacts\audit.json
```

It does not start the web service, call an LLM, or access the network. It
checks the manifest, Pydantic shape, evidence ownership, period/currency
boundaries, recursive Decimal formulas, rendered numeric claims, and exact
excerpt text. When public source caches are supplied, it also re-hashes the
original filing/market/valuation bytes and re-extracts the cited source. The
optional `--against-corpus` mode performs the same comparison against a local
evidence corpus and domain snapshot.

Exit code `0` means `status=consistent` for the checks that were requested.
`consistent` does not certify the truth of a model-generated narrative: the
report explicitly counts narrative claims that were not semantically rechecked.
Missing public originals are reported as `not_checked`, never silently treated
as verified. A failing check exits with code `1` so a CI job can block a merge.

With both market originals present, `raw_market_cross_check` replays the source
comparison, including the two close values, percentage difference and derived
warnings. `market_sources.checked` counts raw responses, so one dual-source
snapshot can contribute two. When the secondary original is missing, or a
legacy package has derived source warnings/conflicts without the new reference,
`raw_market_cross_check_missing` is `not_checked`; the primary fields are still
checked against their original. A `matched` source status describes the
originals actually checked: also inspect the missing count and individual
`not_checked` entries for incomplete coverage. Rewriting the package checksum
does not defeat comparison against independently supplied raw caches.

## Compatibility

Readers must reject an unknown schema version rather than guessing. New
optional fields may be ignored by a forward-compatible reader, but changing an
existing metric definition or source identity requires a new schema version or
an explicit migration. Historical packages remain byte-stable when they are
re-exported.
