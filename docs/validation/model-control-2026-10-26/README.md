# Model control experiment — 2026-10-26

Same 20-question stratified set used for the frozen live check:

- 4 numeric, 4 causality, 4 risk, 4 negation, 4 cross-period
- same 62-filing corpus copy and source caches
- same `glm-5.3-flash` review model
- same `300s` research budget, `30s` review reserve, token and retry settings
- same agent system prompt and tool registry
- one worker per arm; fresh home for each arm
- author models: `deepseek-v4.1-flash` vs `mimo-v2.6-flash`
- provider temperature/top_p/seed were not explicitly set; provider defaults were used

## Measured result

| Metric | DeepSeek run 1 | DeepSeek run 2 | Mimo run 1 | Mimo run 2 |
|---|---:|---:|---:|---:|
| answered / partial / abstained | **11 / 9 / 0** | **13 / 7 / 0** | **11 / 9 / 0** | **15 / 5 / 0** |
| coverage | 0.55 | 0.65 | 0.55 | 0.75 |
| P50 / P95 | 43.8s / 147.8s | **39.3s / 104.3s** | 47.8s / 152.1s | 59.6s / 250.3s |
| avg tool calls/question | 7.85 | **8.05** | not captured | 6.55 |
| direct-text rounds | 0 | **0** | not captured | 0 |
| model calls/question | 5.7 | 5.35 | 5.05 | 5.6 |
| reported tokens | not captured | **3,188,316** | not captured | not captured |
| numeric mismatch | 0 | 0 | 0 | 0 |
| citation binding | 1.0 | 1.0 | 1.0 | 1.0 |
| timeout / runner errors | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

DeepSeek mean: **12 answered / 8 partial**; Mimo mean: **13 answered / 7 partial**.
Both are below the 16/20 target. DeepSeek’s observed latency is lower in both
runs, but its complete rate is not stable either: 11/9 → 13/7.

Per-question flips:

- DeepSeek: **4 status flips** between runs.
- Mimo: **6 status flips** between runs.
- No tool-call failure, direct-text answer, timeout or runner error in either
  arm; the differences are in partial/full completion and evidence handling.

## What this rules out

- **Not “DeepSeek does not call tools”:** DeepSeek averaged more tool calls
  (7.85/8.05 vs Mimo 6.55).
- **Not “DeepSeek answers directly without tools”:** both arms had zero
  direct-text rounds; model rounds ended with `tool_calls`.
- **Not a review-model or budget difference:** same review model, budget,
  headers/tools and retry settings.
- **No timeout or runner error** in either arm.
- **Single-run comparisons are not reliable:** Mimo varied from 11/9 to 15/5
  and DeepSeek from 11/9 to 13/7. Both swings exceed the three-answer threshold.

## What remains

The arms did not have identical retrieval sets: the agents issued different
search queries and used different numbers of tool calls. This is a model-
behavior comparison on the same corpus, not a fixed-replay comparison.

Prompt fit and model reasoning remain possible. A three-prompt A/B and repeated
runs are required before changing defaults. DeepSeek’s observed speed advantage
is real in these runs, but its complete rate is not stably above Mimo, and
neither model reaches 16/20 in both runs.

## Conclusion

- Do not switch the global model to DeepSeek based on this experiment.
- Do not run the 140-question set yet.
- Next: three-prompt A/B on 10 stratified questions, then choose model/routing
  only if the result is stable across repeats.
- The low strict full-support rates remain an answer-proof problem, not just a
  model-choice problem.

## Files

`deepseek-*` and `deepseek-r2-*` are the two DeepSeek runs; `mimo-*` is the
Mimo trace run. Each contains the gold subset, checkpoint, results, failures,
report and request log. `trace_summary` records tool calls, direct-text rounds,
model steps, author/review time and finish reasons. The first DeepSeek run and
Mimo run predate token instrumentation; the second DeepSeek run includes
3,188,316 reported tokens.
