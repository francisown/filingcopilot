# Model control experiment — 2026-10-26

Same 20-question stratified set used for the frozen live check:

- 4 numeric, 4 causality, 4 risk, 4 negation, 4 cross-period
- same 62-filing corpus copy and source caches
- same `glm-5.3-flash` review model
- same `300s` research budget, `30s` review reserve, token and retry settings
- same agent system prompt and tool registry
- one worker per arm; fresh home for each arm
- author models: `deepseek-v4.1-flash` vs `mimo-v2.6-flash`

## Measured result

| Metric | DeepSeek run 1 | DeepSeek run 2 | Mimo run 1 | Mimo run 2 |
|---|---:|---:|---:|---:|
| answered / partial / abstained | **11 / 9 / 0** | **13 / 7 / 0** | **11 / 9 / 0** | **15 / 5 / 0** |
| coverage | 0.55 | 0.65 | 0.55 | 0.75 |
| P50 / P95 | **43.8s / 147.8s** | **53.1s / 132.4s** | **47.8s / 152.1s** | **59.6s / 250.3s** |
| avg tool calls/question | 7.85 | 8.55 | not captured | 6.55 |
| direct-text rounds | 0 | 0 | not captured | 0 |
| model calls/question | 5.7 | 5.7 | 5.05 | 5.6 |
| reported tokens | not captured | 2,870,956 | not captured | not captured |
| numeric mismatch | 0 | 0 | 0 | 0 |
| citation binding | 1.0 | 1.0 | 1.0 | 1.0 |
| timeout / runner errors | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

## What this rules out

- **Not “DeepSeek does not call tools”:** DeepSeek averaged more tool calls
  (7.85 and 8.55 vs Mimo 6.55).
- **Not “DeepSeek answers directly without tools”:** both arms had zero
  direct-text rounds; all model rounds ended with `tool_calls`.
- **Not a review-model or budget difference:** both used the same review model,
  same budget, same headers/tools and same retry settings.
- **No timeout or runner error** in either arm.
- **Single-run comparisons are not reliable:** Mimo varied from 11/9 to 15/5
  and DeepSeek from 11/9 to 13/7. Mimo’s four-answer swing exceeds the
  three-answer threshold; neither run is a stable 16/20.

## What remains

The arms did not have identical retrieval sets: the agents issued different
search queries and used different numbers of tool calls. The comparison is
therefore a model-behavior comparison on the same corpus, not a fixed-replay
comparison.

Mimo itself changed from **11/9** to **15/5**, and DeepSeek from **11/9** to
**13/7**, with the same models and configuration. Single-run differences are
therefore not enough to attribute the complete-rate gap to DeepSeek or to
prompt fit. The stable answer set is nine numeric/cross-period/causality-001
items; six items change status between runs. DeepSeek’s 20-question results are
not stable above Mimo, and neither model reaches 16/20 in both runs.

## Conclusion

- The interface/tool-call failure hypotheses are not supported by this trace.
- Prompt fit and model reasoning remain possible, but a three-prompt A/B and
  repeated runs are required before changing defaults.
- Do not switch the global model to DeepSeek based on this run. Do not run the
  140-question set yet.

## Files

`deepseek-*`, `deepseek-run2-*` and `mimo-*` contain the gold subset,
checkpoint, results, failures, report and request log for each arm.
`trace_summary` records tool calls, direct-text rounds, model steps,
author/review time and finish reasons. The first runs predate token
instrumentation; DeepSeek run 2 includes 2,870,956 reported tokens.

## Next

Do not run prompt A/B yet as a model-selection decision. The variance is larger
than the effect being measured. The next experiment must repeat each prompt
arm at least twice, or use a fixed tool-result replay so retrieval is identical.
Until then, keep Mimo as the default and do not run 140 questions.
