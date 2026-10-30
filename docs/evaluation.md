# Evaluation

This page records the release-facing measurements. Raw intermediate runs and
provider request logs stay local; only the latest full report and the small
retrieval snapshot are kept as public validation records.

## Latest full workflow record

The [machine-readable report](validation/live-full-deepseek-mimo-2026-10-30-rerun/report.json)
contains the configuration fingerprint, denominators and run metadata. It used
the 140-question bilingual set, a 2026-10-08 knowledge date, DeepSeek for
research, Mimo for review, the diversity reranker, two workers and a 3,000-second
cooperative research ceiling.

| Metric | Result |
|---|---:|
| Captured / eligible rows | 140 / 140 |
| Answerable coverage | **91 / 100** |
| Expected insufficient-evidence refusals | **20 / 20** |
| Market-source policy cases | **20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Citation binding on delivered rows | **1.000** |
| Model-backed P50 / P95 | **90.6 s / 174.9 s** |
| Reported tokens | **3,753,566** |
| Runner errors / deadline timeouts | **0 / 0** |

The run predates the latest placeholder-token retry expansion. That fix was
verified with targeted API and regression tests, but it is not counted as a new
full-run score. The measured 91/100 must therefore remain the public population
result until another complete run is deliberately made.

The 100 answerable rows cover numeric, cross-period, causality, negation and risk
questions. The remaining 40 rows are designed refusals or market-source policy
cases and are reported separately. `citation_binding_rate` means that an
evidence ID was observed and bound; it is not an independent human judgment of
semantic entailment.

## Targeted post-change check

After the narrative requirement, reviewer-context and bounded synthesis-retry
changes, the eight previously incomplete narrative rows were rerun together
with one previously answered control row. This was a single stochastic,
single-worker DeepSeek research / Mimo review run; it is a diagnostic and does
not replace the 140-question population result above.

| Metric | Result |
|---|---:|
| Captured rows | **9 / 9** |
| Answered / partial | **4 / 5** |
| Previously partial rows that became answered | **4 / 8** |
| Unsupported claims / numeric errors | **0 / 0** |
| Runner errors / deadline timeouts | **0 / 0** |
| Model-backed P50 / P95 | **168.0 s / 331.4 s** |
| Reported tokens | **607,852** |

One previously answered control row became partial in this run, so the result
shows a promising failure-path improvement but is not sufficient to claim a
global accuracy increase. The remaining partial rows were limited by missing
direct filing attribution, document-wide negative evidence, or another
unfinished model synthesis.

## Retrieval

The [latest retrieval snapshot](validation/retrieval-diversity-2026-10-29.json)
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

The latest full run is a model-assisted workflow measurement, not an independent
human gold standard. Causality questions can remain partial when a filing names
several drivers but does not disclose each driver's contribution to a consolidated
margin change. Negation questions remain conservative when a selected passage
cannot establish a document-wide absence. External answer-support scores are
strict and should not be compared with older any-number-match figures.

## Reproduce the local checks

```powershell
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

The full live evaluation requires the caller's own provider configuration and is
intentionally not run as part of CI. API keys and local corpora are excluded by
`.gitignore`.
