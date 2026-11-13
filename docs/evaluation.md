# Evaluation

This page records the frozen pre-optimization baseline and the latest
post-optimization reruns. Raw intermediate runs and provider request logs stay
local; the full report, results, failures and numeric audit are kept as the
machine-readable validation record.

## Frozen pre-optimization baseline (2026-11-02)

The [machine-readable report](validation/live-full-deepseek-mimo-2026-11-02-final/report.json)
contains the configuration fingerprint, denominators and run metadata. It used the
140-question bilingual set, a 2026-10-08 knowledge date, DeepSeek for research,
Mimo for review, the diversity reranker, four workers (four concurrent question runs), a 3,000-second cooperative
research ceiling, a 300-second review timeout and a 16,384-token review budget.
Those review limits are the pre-optimization configuration recorded by that run.

| Metric | Result |
|---|---:|
| Captured / eligible rows | 140 / 140 |
| Answerable coverage | **97 / 100** |
| Expected insufficient-evidence refusals | **20 / 20** |
| Market-source policy cases | **20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Citation binding on delivered rows | **1.000** |
| Citation binding across all rows | **0.857** |
| Model-backed P50 / P95 | **237.7 s / 842.1 s** |
| Reported tokens | **4,868,227** |
| Runner errors / deadline timeouts | **0 / 0** |
| Raw SEC numeric | **100 matched / 0 mismatched / 27 unassessed** |
| Current regression suite | **2,360 passed** |

Three answerable rows delivered partial answers: `semantic-causality-015`,
`semantic-causality-019` and `semantic-risk-018`. They retain explicit gaps
rather than unverified completion; all three are coverage-review outcomes in
this run. The negation rows are answered, including the explicit-negative
subject/period mismatch case.

### Post-optimization rerun

A 140-question rerun with the review changes and a 16,384-token review ceiling
produced model-backed **P50 155.1 s / P95 586.7 s**, **96/100 answerable rows**,
**406 claims**, no review `length` rows, and zero unsupported or numeric errors.
It is faster than the frozen baseline (237.7 s / 842.1 s) but not a clean quality
win: the baseline answered 97/100 with 407 claims, and the rerun had four partial
answerable rows.

A further rerun skipped answer-coverage review on non-final repair passes. It
produced **P50 121.3 s / P95 464.6 s**, **98/100 answerable rows**, **394 claims**,
two review `length` turns, and zero unsupported or numeric errors. Coverage and
speed improved, but claims fell by 12 and two negation answers are intentionally
short (one claim each); this is a speed/coverage trade-off, not a free win. The
faster 8,192-only run (P95 618.3 s, 98/100) was rejected because it silently
dropped four claims from each of two negation answers; an 8,192-plus-escalation
run was slower and had lower coverage. See the
[latency budget](latency-budget.md) for the per-configuration comparison.

The latency tail is concentrated in the independent review stage, not retrieval:
review accounts for 78.0% of model-backed row latency at P50. See the
[latency budget](latency-budget.md) for the stage breakdown and the optimization
sequence. Future runs also record `review_calls`, `retry_wait_ms` and `tool_ms`
in `trace_summary`, so review retries and pacing can be separated from model
generation.

The 100 answerable rows cover numeric, cross-period, causality, negation and risk
questions. The remaining 40 rows are designed refusals or market-source policy
cases and are reported separately. `citation_binding_rate` means that an evidence
ID was observed and bound; it is not an independent human judgment of semantic
entailment. These are engineering and workflow measurements, not an independent
human accuracy study.

## Retrieval

The [2026-10-29 frozen retrieval snapshot](validation/retrieval-diversity-2026-10-29.json)
uses 200 bilingual questions with exact passage gold data, embedding disabled and
the diversity reranker.

| Metric | Result |
|---|---:|
| Span recall@5 | **0.615** |
| Page recall@5 | **0.635** |
| Nugget recall@5 | **0.710** |
| P50 / P95 | **66 / 168 ms** |

The reranker changes only the top near-duplicate candidates. It improves page and
numeric-nugget recall over the anchor ordering without adding a model call. This
is a retrieval measurement, not an answer-accuracy score.

A 2026-11-12 local rerun on the current corpus used the lexical fallback because
the optional dense model could not be downloaded in that environment. With the
tuned diversity section penalty and multi-query off it measured **span@5 0.475 /
page@5 0.500 / nugget@5 0.556 / exact-ID recall@5 0.410**, with
**equivalence recall@5 0.995** and P50/P95 **65 / 104 ms**. The untuned diversity
setting measured 0.460 / 0.475 / 0.551; multi-query on produced the same recall
but P95 **199 ms**. Of the 200 gold items, 146 expected passage IDs were rebound
to the current corpus by quote overlap; quote/page labels were unchanged. The
current corpus has a median of 31 equivalent passages per question (the snapshot
had 17), so compare equivalence recall and page-set semantics, not raw exact IDs,
across corpus versions. A BGE hybrid and a MiniLM top-20 cross-encoder were also
tested; neither improved the page/span/nugget trade-off, so lexical diversity
remains the default. A dense pilot is not reported as a win until a
domain-specific model is cached and validated.

## External reference sets

These records are separate from the live workflow and use their own gold data:

- [FinanceBench full-filing retrieval](validation/financebench-full-2026-10-26.json)
- [FinanceBench answer support](validation/financebench-support-2026-10-26.json)
- [FinDER answer support](validation/finder-support-2026-10-26.json)

For answer support, `full / partial / unsupported` is the strict classification.
The evaluable subset is the only denominator that can be interpreted as answer
support: its gold answer must be fully supported by its gold evidence. A raw
full rate also includes rows whose reference evidence cannot support the gold
answer and is therefore not a model-accuracy estimate.

## What the checks prove

- Typed financial facts and calculations are checked for ticker, metric, unit,
  period and arithmetic consistency.
- Narrative claims must cite observed filing passages and pass deterministic scope
  gates plus the configured review model.
- Unsupported or unverified claims are withheld instead of being counted as
  correct.
- Designed insufficiency questions must return a specific limitation rather than
  a fabricated answer.
- A saved research package can be verified offline; package verification proves
  consistency of the saved evidence, not semantic correctness.

## Known limitations

The published full run is a model-assisted workflow measurement, not an independent
human gold standard. Causality questions can remain partial when a filing names
several drivers but does not disclose each driver's contribution to a consolidated
margin change. Negation questions remain conservative when a selected passage
cannot establish a document-wide absence. External answer-support scores are
strict and should not be compared with older any-number-match figures.

## Reproduce the local checks

```powershell
python -m pip install -e ".[dev,semantic,evaluation]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

The full extras match CI. A core `.[dev]` install still collects the suite;
optional evaluation and embedding tests skip explicitly when their packages are
absent. The full live evaluation requires the caller's own provider
configuration and is intentionally not run as part of CI. API keys and local corpora are excluded by
`.gitignore`.
