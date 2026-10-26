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

| Metric | DeepSeek | Mimo (this run) | Mimo (previous run) |
|---|---:|---:|---:|
| answered / partial / abstained | **11 / 9 / 0** | **15 / 5 / 0** | **11 / 9 / 0** |
| coverage | 0.55 | 0.75 | 0.55 |
| P50 / P95 | **43.8s / 147.8s** | **59.6s / 250.3s** | **47.8s / 152.1s** |
| avg tool calls/question | **7.85** | **6.55** | not captured |
| direct-text rounds | **0** | **0** | not captured |
| model calls/question | 5.7 | 5.6 | 5.05 |
| numeric mismatch | 0 | 0 | 0 |
| citation binding | 1.0 | 1.0 | 1.0 |
| timeout / runner errors | 0 / 0 | 0 / 0 | 0 / 0 |

## What this rules out

- **Not “DeepSeek does not call tools”:** DeepSeek averaged more tool calls
  (7.85 vs 6.55).
- **Not “DeepSeek answers directly without tools”:** both arms had zero
  direct-text rounds; all model rounds ended with `tool_calls`.
- **Not a review-model or budget difference:** both used the same review model,
  same budget, same headers/tools and same retry settings.
- **No timeout or runner error** in either arm.

## What remains

The arms did not have identical retrieval sets: the agents issued different
search queries and used different numbers of tool calls. The comparison is
therefore a model-behavior comparison on the same corpus, not a fixed-replay
comparison.

Mimo itself changed from **11/9** (previous run) to **15/5** (this run) with the
same model and configuration, so single-run differences are not enough to
attribute the complete-rate gap to DeepSeek or to prompt fit. DeepSeek’s first
20-question result is worse than this Mimo run but equal to the previous Mimo
run.

## Conclusion

- The interface/tool-call failure hypotheses are not supported by this trace.
- Prompt fit and model reasoning remain possible, but a three-prompt A/B and
  repeated runs are required before changing defaults.
- Do not switch the global model to DeepSeek based on this run. Do not run the
  140-question set yet.

## Files

`deepseek-*` and `mimo-*` contain the gold subset, checkpoint, results, failures,
report and request log for each arm. `trace_summary` in each result records
tool calls, direct-text rounds, model steps, author/review time and finish
reasons.
