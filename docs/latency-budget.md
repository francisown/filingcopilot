# Latency and cost budget

The default cooperative research ceiling is **3,000 seconds**. It is the
effective wall-clock limit for every question shape; narrative routes do not
silently extend it. Set `FC_RESEARCH_TIMEOUT=0` or `none` only for an explicitly
unlimited local run.

## Latest measured workflow

The latest full record used DeepSeek for research and Mimo for review. Its
model-backed latency was **90.6 seconds P50 / 174.9 seconds P95** across the
model-using questions. The overall median is much lower because deterministic
numeric, cross-period, refusal and policy rows do not call the model.

The run had no runner errors or deadline timeouts. This does not mean that every
question is fast: model sampling can change the tail between runs. The complete
measured values are in the [latest workflow report](validation/live-full-deepseek-mimo-2026-10-30-rerun/report.json).

## Retrieval versus generation

The current retrieval snapshot is **66 ms P50 / 168 ms P95**. External checks are
also much faster than model-backed research: FinDER retrieval is in the hundreds
of milliseconds and full-filing reranking is in the low seconds. The main latency
problem is therefore serial model/tool orchestration, not passage lookup.

## Cost controls

Usage reports always retain provider-reported token counts. Dollar estimates are
emitted only when both `FC_MODEL_INPUT_PRICE_PER_MILLION` and
`FC_MODEL_OUTPUT_PRICE_PER_MILLION` are configured. `FC_MAX_COST_USD` must be
used with both prices; the application rejects a partial cost configuration
instead of displaying a ceiling that cannot be enforced. With no prices, cost
remains unconfigured rather than being guessed.

## Design constraints

- Numeric and cross-period questions use deterministic paths whenever possible.
- Tool calls and reasoning rounds remain bounded even when the wall-clock ceiling
  is disabled.
- A deadline produces a partial result with explicit gaps; it never silently
  converts an unfinished explanation into a complete answer.
- Retrieval changes are accepted only after an offline gold comparison; the
  current diversity reranker is retained because it improves page/nugget recall
  without a model call.

The latency target for a polished interactive experience remains below 30 seconds
for a focused question. The current measured P95 is above that target, so further
speed work should reduce or parallelize model/review rounds and must be validated
with a paired quality check.
