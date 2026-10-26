# Frozen 20-question live check — 2026-10-26

Source code: `f2f12f4` (tag `eval-freeze-f2f12f4`).

Configuration recorded in `../freeze-f2f12f4-2026-10-26.json`:

- `mimo-v2.6-flash` author model, `glm-5.3-flash` independent review model
- `research_timeout=300s`, `review_reserve_seconds=30s`
- fresh temporary home seeded from the existing 62-filing corpus and caches
- `as_of=2026-10-08`, one worker to avoid concurrent SQLite initialization
- gold SHA256 `831bee05f2aaea1524f689eb65361c985f0d7ceceea3dc723276916bda2e41b8`

## Result

| Metric | Value |
|---|---:|
| Questions | 20 |
| Answered / partial / abstained | **11 / 9 / 0** |
| Coverage rate | **0.55** |
| Numeric mismatch rate | **0.0** |
| Citation support / binding | **1.0 / 1.0** |
| P50 / P95 latency | **47.8s / 152.1s** |
| Timeout / runner errors | **0 / 0** |
| Historical same 20 answered/partial | **17 / 3** |

**Latency passed; complete-rate acceptance failed.** The current run had 6 more
partial answers than the historical same-ID set. Do not present this as a
quality improvement and do not run the 140-question set until the partial
regressions are explained.

The empty-home smoke run is not part of this record: it initialized only six
filings and produced 10 answered / 6 abstained / 4 partial, so it is a cold-start
observation, not a comparable quality measurement.

## Files

- `gold20.json` — selected IDs and questions
- `checkpoint.json` — per-question checkpoint
- `results.json` — captured result rows
- `failures.json` — rows below the acceptance criteria
- `report.json` — evaluator report and run context
- `run.log` — request/progress log
