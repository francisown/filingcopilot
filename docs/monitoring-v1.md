# FilingCopilot Monitor v1

Status: implementation baseline. This document freezes the scope, event schema,
discovery rules, evidence locator, case states, labeling protocol and acceptance
gates for the first monitoring release.

## Product

FilingCopilot Monitor is a local disclosure-monitoring workspace for analysts who
cover 10–30 US-listed companies. It answers one question every day:

> What changed in the filings I follow, which changes are material, where is the
> evidence, and what should I check next?

A brief is a **reviewable claim set**, not a recommendation. Every factual or
calculated statement carries an evidence ID. Inference is labelled separately and
never counted as a filing fact.

## Scope

In scope:

- US domestic issuers only (10-K, 10-Q, 8-K and their `/A` amendments).
- Financial results from periodic reports.
- Liquidity and debt changes.
- Material 8-K events (item-code driven).
- Legal and regulatory changes disclosed in 8-K items or filing sections.

Out of scope for v1:

- 20-F / 6-K monitoring, China/HK markets, multi-market.
- Full-text diff of an entire filing.
- Guidance extraction beyond an explicit 8-K Item 2.02 exhibit.
- Customer concentration and supply-chain extraction.
- Auto-trading, credit decisions, multi-tenant SaaS, price prediction.
- More MCP tools and multi-agent orchestration.

## Event taxonomy

Event types are multi-label. A single filing can produce several events.

| event_type | Examples | Primary source |
|---|---|---|
| `financial_results` | revenue, profit, cash flow, margins, EPS | 10-K / 10-Q facts, 8-K 2.02 |
| `liquidity_debt` | cash, debt, covenants, credit facilities | balance-sheet facts, 8-K 1.01/2.03/2.04 |
| `legal_regulatory` | litigation, regulator action, cyber incident | 10-K legal proceedings, 8-K 1.05/8.01 |
| `guidance` | explicit outlook change | 8-K 2.02 + EX-99.1 only |
| `corporate_change` | leadership, control, delisting, impairment | 8-K 2.01/2.05/2.06/3.01/5.01/5.02 |
| `other_material` | Reg FD / other events with no better class | 8-K 7.01/8.01 |

8-K item codes are the deterministic classifier. Item `9.01` (exhibits) alone is
never an event. Items are parsed from the SEC submissions JSON before any model
call.

## Comparison basis

Every event records `comparison_basis` and `prior_filing_id`:

- `prior_annual` — 10-K vs the previous 10-K.
- `prior_quarter` — 10-Q vs the previous 10-Q.
- `prior_year_quarter` — 10-Q vs the same quarter one year earlier.
- `prior_8k` — 8-K vs the previous 8-K when the same item code exists.
- `first_observation` — no comparable prior filing.

Single-quarter and year-to-date (YTD) figures are never mixed. Cash-flow figures
that are YTD-only are labelled as YTD, not converted to a quarter.

## Evidence locator

A locator is stable across HTML/PDF renderers:

```text
accession + section/item + quote + section-relative char offset + quote hash
```

`page` is display-only. XBRL facts use `concept + context + unit + accession`
instead of a page. `PARSER_VERSION` is stored with every snapshot and brief.

## Data objects

- `monitoring_policies` — per-ticker watch items, forms, materiality floor.
- `monitoring_state` — per-ticker watermark (`last_accepted`) and last scan.
- `monitoring_filings` — per-accession `discovered` / `pending` / `processed` /
  `suppressed` / `failed` status, attempt count and last error. New accessions
  are registered before download; a failed download remains retryable. Pending
  and failed 8-K accessions are re-fetched by exact accession, not just by the
  latest-five window.
- `monitoring_digests` — daily digest delivery records and idempotency keys.
- `monitoring_runs` — one scan/ingest attempt with counts, errors and timing.
- `filing_snapshots` — immutable section text + hash + parser version.
- `filing_diffs` — derived facts/section deltas + comparison basis + versions.
- `disclosure_events` — extracted event with facts, inferences, gaps, materiality.
- `research_cases` — one filing-level review case containing 1–N events.
- `case_reviews` — approve/edit/dismiss/feedback actions with reason codes.
- `monitoring_audit` — immutable action log.
- `notification_deliveries` — optional email/Slack delivery record.

`event.status` (`open` / `superseded`) is separate from `case.status`
(`pending_review` / `approved` / `edited` / `dismissed` / `needs_followup`).

## Materiality

Materiality is deterministic in v1; an LLM never decides it.

- 8-K items 1.01, 1.02, 1.03, 1.05, 2.01, 2.02, 2.03, 2.04, 2.05, 2.06, 3.01,
  4.02, 5.01, 5.02: high or medium by item map.
- Revenue / net income / operating cash flow: high requires **both** ≥5% and
  ≥$10M; medium requires both ≥2.5% and ≥$5M. Either condition alone is not
  material.
- Gross / operating / net margin: ≥200 bps high; ≥100 bps medium.
- Cash / debt: ≥10% high; ≥5% medium.
- Section change: similarity < 0.90 high; < 0.97 medium, only for Risk Factors,
  MD&A, Liquidity, Legal Proceedings.
- A filing with only item 9.01 or no material change is suppressed from briefs
  and recorded with a suppression reason.

Every event stores `materiality_inputs` and `materiality_rule_hits` so a false
positive can be replayed.

## Brief

Fixed sections:

1. What happened.
2. Largest numeric changes (fact / calculation).
3. Risks and operating factors that changed.
4. Why it may matter (inference; basis evidence required).
5. Sources for every conclusion.
6. Explicit gaps.
7. What the analyst should check next.

At most one bounded model call is allowed per brief. The brief path uses
`OpenAIGateway.complete_once`: one actual provider request, no retry/failover,
with `FC_MONITORING_BRIEF_TIMEOUT`, `FC_MONITORING_BRIEF_MAX_TOKENS` and optional
`FC_MONITORING_BRIEF_MAX_COST_USD`. It may rewrite the headline, `why_matters`
and section semantic summaries only. Facts, calculations and citations are
generated deterministically. A model failure or budget breach falls back to the
template.

Section deltas and passage evidence carry `char_start`, `char_end`,
`char_text`, `section_text` and `quote_hash`; semantic summaries are labelled
`excerpt` or `model`. The offline verifier rejects negative, empty or out-of-range
char spans and checks the exact text slice; quote hashes are checked for both
passages and section excerpts.

The brief cost cap is a **pre-flight** check: estimated input plus the full
output token ceiling is priced before the request. Enabling
`FC_MONITORING_BRIEF_MAX_COST_USD` without both model prices is rejected at
startup rather than silently skipped.

## Extraction guardrails

- `WatchPolicy.watch` filters event types before a case is created. Watching only
  `debt` suppresses financial-result events.
- Guidance requires an 8-K Item 2.02 plus an EX-99.1 passage with a
  forward-looking statement that names a concrete period or numeric target.
  Percent targets such as “growth to 10% in 2027” count. Disclaimers
  (“forward-looking statements”, “actual results may differ”), explicit
  refusals and bare keywords are rejected.
- 8-K evidence is bound per item: each event cites passages matching its item
  code/keywords. If no passage matches, the event keeps an evidence gap and no
  cover/unrelated paragraph is substituted.
- A liquidity event is suppressed when cash/debt changes are immaterial; it is
  not emitted at a fixed `medium` level.
- Risk Factors changes produce a `risk_factor_change` event under
  `legal_regulatory`, in addition to MD&A, liquidity and legal branches.
- No comparison version is mixed: current facts must belong to the current
  accession; prior facts must have been filed on or before the current filing.
  Instant balance-sheet facts are handled separately from duration/YTD facts.
  Gross/operating/net margin is calculated only when numerator and revenue
  share start date, end date, period kind and unit; otherwise a gap is reported.
- Nested filings preserve `Passage.parent_section`, so a Risk Factors paragraph
  under a sub-heading still maps to `risk_factors` in the real HTML path.

## Parser version

`PARSER_VERSION` was bumped to `2026-11-11.1` for the parent-section change.
Existing cached parses are treated as stale and re-extracted on next use, so old
Risk Factors paragraphs receive `parent_section` rather than remaining
classified as `other`.

## Case state machine

```text
pending_review → approved
               → edited
               → dismissed
               → needs_followup
approved / edited / dismissed → pending_review (reopen only with a reason)
```

One case per filing. Approve/edit/dismiss/feedback append to `case_reviews` and
`monitoring_audit`. Edits store a unified diff and the edited brief.

Each brief has a `brief_version`. Approve/dismiss/edit must carry the version
the reviewer saw; a mismatched version is rejected. Regeneration increments the
version, preserves the old `approved_brief`/`approved_version`, returns the case
to `pending_review`, and writes a `regenerate` review. A new generation can
therefore never silently inherit an old approval. The version-conditional case
update, review row and audit row share one SQLite transaction. A monotonic
`revision` increments on every review or regeneration, so both races are
covered: an old review cannot overwrite a new brief, and a regeneration read
before an approval cannot overwrite the approval either.

## Discovery

- Poll `https://data.sec.gov/submissions/CIK{cik}.json` with a short TTL cache,
  SEC fair-access pacing and the configured SEC User-Agent.
- Newness is based on `acceptanceDateTime`, not `filingDate`.
- Deduplicate by accession number. Amendments link to `amends_filing_id`.
- Reuse the existing discovery/ingestion pipeline for extraction; do not
  re-implement HTML parsing.
- A failed ticker does not abort the scan; it is recorded in `monitoring_runs`.
- SEC fair-access pacing and retries are owned by `SecFetcher`.
- The first scan is a bootstrap: it processes the latest 10-K / 10-Q and the
  recent 8-K batch (last 7 days relative to the newest SEC acceptance). Later
  scans use the `monitoring_state.last_accepted` watermark and process only
  filings accepted after it. The watermark is not advanced when acquisition
  produced a warning, so the next scan retries.
- Every accession has its own `monitoring_filings` status:
  `discovered`, `pending`, `processed`, `suppressed` or `failed`. A failed/pending
  accession is retried on every scan even after newer filings succeed. The normal
  scan still reads the latest five notices; pending/failed 8-K accessions are
  re-fetched by exact accession so a six-or-more-item backlog can drain.
- A full historical reprocess is an explicit `reprocess=true` action; it rebuilds
  only filings that already have a case.
- The optional background loop is enabled with
  `FC_MONITORING_SCAN_INTERVAL_MINUTES` (0 = off). It waits one interval before
  the first automatic scan.

## Historical replay

`POST /api/monitoring/replay` (or `monitor-replay`) imports the requested date
window: it discovers SEC metadata, fetches 8-K notices across the full requested
window (up to the 730-calendar-day, 20-notice safety budget), refreshes
quarterly/annual filings, and rebuilds one case per in-range filing. The SEC
item codes are retained on `Filing.items`, so archive filings without a recent
submissions row still reach the 8-K classifier. `reprocess` is not required.
Replay is intentionally separate from the live watermark and does not advance
it.

Amendments (`10-K/A`, `10-Q/A`, `8-K/A`) link to the original filing through
`amends_filing_id`. Same-day originals are linkable; if multiple base filings
share the report period, the link is left empty and a gap is recorded instead of
guessing. Processing an amendment marks the original filing's open events
`superseded` with `superseded_by_event_id`. Re-running an original after an
amendment cannot reopen its superseded events: `upsert_event` preserves the
superseded status, and `amending_event_ids` reapplies the link.

## Daily digest and delivery

`GET /api/monitoring/digest` renders a Markdown morning brief from cases updated
in the date window. If no explicit start is supplied, the window begins at the
**cutoff captured before the previous successful query** (or the previous day on
a first run), so updates made while the previous email was sending are not lost.
Cases whose policy has `notify=false` or `enabled=false` are excluded.

`POST /api/monitoring/digest/deliver` supports `local`, `webhook`, native
`slack` (incoming webhook `text`) and `smtp`; `GET /api/monitoring/digest.pdf`
returns a ReportLab PDF. `FC_MONITORING_DIGEST_ENABLED` +
`FC_MONITORING_DIGEST_HOUR` start the daily loop inside `serve`. SMTP uses
`FC_SMTP_HOST/PORT/USER/PASSWORD/FROM/TO/STARTTLS`; Slack uses
`FC_SLACK_WEBHOOK_URL`. A digest is claimed atomically in `monitoring_digests`,
so concurrent manual/scheduled sends cannot both send. The claim distinguishes
`sent` (`already_sent`) from `sending` (`in_progress`); a failed claim can be
retried, and `force=true` bypasses idempotency.

The web case view edits the full brief (headline, narrative, numbers JSON,
inferences, gaps and next checks), not just the headline. Saves go through the
same versioned edit endpoint, so approval snapshots are never replaced.

## Offline verification and delivery

`GET /api/monitoring/cases/{id}/package` exports a case package with a SHA256
manifest. `POST /api/monitoring/verify` (or `monitor-verify <path>`) checks the
manifest, case state, source IDs, fact arithmetic, unit agreement and quote
binding offline. It checks every physical number row, rejects duplicate metric
keys inside one brief or one event, and compares the brief rows with the event
facts. The same fact may be reused by different events, provided its values are
identical. It reports integrity and content status separately. It proves package
consistency, not semantic correctness.

Verify the **exact exported file bytes** (for example with
`Invoke-WebRequest -OutFile`). Re-serializing with PowerShell `ConvertTo-Json`
can lose floating-point digits; that is a lossy transformation, not a package
defect.

The event scorer is `filingcopilot.monitoring.evaluation.score_events`; run it
over a gold/predicted pair with `scripts/evaluate_monitor_events.py`. It reports
per-type and overall precision/recall with Wilson 95% confidence intervals, a
no-event false-positive rate, and the missing/false-positive keys. CLI gates:
`--min-recall`, `--min-recall-lower`, `--max-fp-rate`, `--max-no-event-fp`.
The seed file `evaluation/monitor_events_v1.json` is synthetic; the 150–200
event analyst gold set and no-event controls remain a pilot deliverable.

`POST /api/monitoring/cases/{id}/deliver` records a `local` delivery or posts the
markdown brief to `FC_MONITORING_WEBHOOK_URL`. Delivery rows are persisted in
`notification_deliveries` with status `recorded`, `sent` or `failed`.

## Evaluation

- Dev set: 40–60 filings for fast iteration. Not published as accuracy.
- Pilot gold set: 150–200 labelled events plus an equal no-event control set,
  with 30% held out from threshold tuning.
- Two annotators, blind to system output, plus adjudication; report Cohen's κ.
- Report `fact`, `calculation`, `inference` and `gap` separately.
- A claim is sourced only if its evidence ID resolves to the filing, section and
  quote. Inference is never counted as sourced fact.

Acceptance gates for a pilot:

| Metric | Gate |
|---|---|
| Event recall (per class) | ≥80% point estimate; Wilson 95% lower bound ≥70% |
| Material false positives | ≤25% on no-event controls |
| Numeric audit | 100% exact after unit/rounding normalization |
| Citation binding | 100% of fact/calculation claims |
| Warm brief P95 | <90 s (filing already local) |
| Cold ingest | <5 min |
| Model calls per brief | ≤1 |
| Analyst acceptance | ≥3 analysts, ≥100 briefs; ≥60% approve without edit |
| Discovery SLO | 0 missed filings in a 2-week audit; 95% within 30 min |

## Rollout

1. Phase 0: interviews, 10 manual briefs, one sustained pilot user.
2. Phase 1: discovery, incremental ingest, run observability.
3. Phase 2: facts diff, 8-K classification, evidence binding, template brief.
4. Phase 3: case queue, review actions, audit, export.
5. Phase 4: shadow mode (no notifications), calibration, then pilot.

## Kill switches

- `FC_MONITORING_ENABLED` (default on for local use).
- `FC_MONITORING_USE_MODEL` (default off; when on, one call per brief).
- Per-ticker policy `enabled=false` stops scans and notifications.
- A scan run records every failure; repeated failures surface in `/api/monitoring/runs`.
