# Latency and cost budget

The default cooperative research ceiling is **3,000 seconds**. It is the
effective wall-clock limit for every question shape; narrative routes do not
silently extend it. Set `FC_RESEARCH_TIMEOUT=0` or `none` only for an explicitly
unlimited local run.

## Latest measured workflow

The latest full record used DeepSeek for research and Mimo for review. Its
model-backed latency was **237.7 seconds P50 / 842.1 seconds P95** across the
model-using questions. The overall median is much lower because deterministic
numeric, cross-period, refusal and policy rows do not call the model.

The run had no runner errors or deadline timeouts. This does not mean that every
question is fast: model sampling can change the tail between runs. The complete
measured values are in the [latest workflow report](validation/live-full-deepseek-mimo-2026-11-02-final/report.json).
These numbers describe the pre-optimization configuration; a new paired run is
required before quoting any improved latency.

## Where the time goes

The frozen run records separate author and review durations for every
model-backed row. Reproduce the summary with:

```powershell
.\.venv\Scripts\python.exe .\scripts\summarize_latency.py `
  docs\validation\live-full-deepseek-mimo-2026-11-02-final\results.json
```

| Stage | P50 | P95 | Share of row latency (P50) |
|---|---:|---:|---:|
| Author model turns | 37.1 s | 209.7 s | 18.6% |
| Independent review | 163.8 s | 782.0 s | 78.0% |
| Entire model-backed row | 237.7 s | 842.1 s | 100% |

The review phase dominates. The same rows use a median of six model calls,
eight tool calls and 7,774 completion tokens; the slowest row used seven model
calls, 14,580 completion tokens and 1,047 seconds of review time. This is not a
retrieval problem. It is serial author/review/repair work, with a review call
that can consume the full 300-second timeout and retry before the run falls
back to withholding the affected claim.

There are three retry layers around a review: the gateway's model attempts, the
verification layer's review retry, and the review transport's format retry. A
failure can therefore be re-entered several times even though the correct
product behavior is to withhold the affected claim and finish. Bounding these
layers is the first latency change; it does not weaken the evidence gate because
a timed-out review never approves a claim.

## Retrieval versus generation

The frozen retrieval snapshot is **66 ms P50 / 168 ms P95**. A 2026-11-12 local
lexical rerun with the tuned diversity section penalty and multi-query off
measured **65 ms P50 / 104 ms P95** with page@5 0.500 and equivalence
recall@5 0.995. External checks are also much faster than model-backed research:
FinDER retrieval is in the hundreds of milliseconds and full-filing reranking is
in the low seconds. The main latency problem is therefore serial model/tool
orchestration, not passage lookup.

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
for a focused question. The current measured P95 is above that target.

Two operational details make the production experience slower than the published
run. The validation run used four workers and `FC_MIN_MODEL_INTERVAL=0`; the
application defaults to two workers and a 21-second per-model pacing interval.
The pacing is a rate-limit guard, not model generation, but it adds up across
author and review calls. The default should be lowered and measured against the
provider's actual 429 rate rather than kept as a blanket delay.

The UI also polls the job every second and receives only stage events plus the
cached numeric preview; it does not stream partial claims or narrative evidence.
The first useful answer therefore waits for the entire review/repair cycle. The
evidence-first step in the optimization sequence closes that perceived-latency
gap without weakening verification.

The current trace still misses several durations: `run_step` (planning and
source preparation), the deterministic `verify` stage, model pacing waits,
initial prefetch steps and per-section company-report batches. Those are the
next instrumentation points before further tuning.

## Optimization sequence

The ordering follows the measured share and the preservation rule: reduce
serial review work first, then author rounds, and only then tune retrieval.

1. **Bounded review retries.** Review now has its own attempt cap
   (`FC_REVIEW_ATTEMPTS=2`, one retry) instead of inheriting the author's retry
   count. The verifier keeps one retry for a transient callback failure, and the
   transport retries a malformed payload once; each layer is bounded rather than
   multiplying a three-attempt gateway chain. A failed review withholds the
   affected review batch (up to five merged claims). This is batch-granular, not
   claim-granular.
2. **Compact review output.** The pre-optimization reviewer returned a scope
   check and a reason per claim plus gap verdicts, with a 16,384-token ceiling.
   The compact contract shortens those fields. The token ceiling is a
   truncation guard, not a latency target; the field `maxLength` values are the
   output contract. Keep the safety ceiling at 16,384: 6,144 and 8,192 both
   produced `finish_reason=length` in full runs and dropped claims. The final
   16,384 full run had no length rows. If a batch still truncates, split it
   rather than re-sending the same oversized request. Do not reduce the evidence
   set to make the prompt smaller.
3. **One repair round.** A rejected claim already has a deterministic fallback:
   withhold it and keep the supported remainder. Allow at most one review-driven
   repair, then finish with explicit gaps. Both the gap-retrieval gate and the
   final repair gate must use the same cap.
4. **Split a failed review batch.** If a review times out or is truncated,
   split the batch in half and retry once against the same evidence. A
   single-claim failure then withholds only that claim. This preserves the cases
   that currently depend on identical retries while avoiding a second full
   timeout. Bound the split depth to one.
5. **Round and tool budgets.** Lower `max_rounds` only after the repair cap is
   measured. Do not lower `max_tool_calls` until initial prefetch is decoupled
   from the budget the agent later spends; otherwise a cause question can lose
   its evidence preparation.
6. **Evidence-first response.** Publish a second preview after initial evidence
   preparation, using only observed source passages and calculations. Do not
   publish unverified narrative claims as preview conclusions. This improves
   perceived latency even when total model time is unchanged.

Each step must be measured on a paired subset of the same gold questions:
model-backed P50/P95, model calls, review calls, completion tokens, delivered
coverage, unsupported claims and refusals. A latency win is rejected if it
changes the verified-answer rate without an explicit trade-off.

For a quick regression check, select six of the slowest negation, causality and
risk rows into a local `artifacts/latency-paired-gold.json` and run the same
evaluation harness with the same worker count and pacing as the baseline. Compare
per-row review time and answered/withheld status; six rows are a signal, not a
population accuracy estimate.

### Paired 6-row result

The first paired run used the compact 6,144-token review ceiling. It reduced the
slow-row subset from P50 776.8 s / P95 1,144.2 s to P50 290.4 s / P95 474.3 s,
but it is not accepted as a quality result:

- `semantic-negation-016` finished review twice with `finish_reason=length` and
  delivered only one claim instead of five.
- `semantic-negation-001` and `semantic-negation-013` moved from answered to
  partial.
- Two other rows improved from partial to answered.

The truncation is a silent quality loss, so an early 8,192 run repeated the same
six rows:

| 6 slow-tail rows | Baseline | 6,144 tokens | 8,192 tokens |
|---|---:|---:|---:|
| P50 | 776.8 s | 290.4 s | **213.3 s** |
| P95 | 1,144.2 s | 474.3 s | **349.6 s** |
| Answered | 4 / 6 | 4 / 6 | **6 / 6** |
| Claims | 25 | 23 | **30** |
| Unsupported / numeric errors | 0 / 0 | 0 / 0 | **0 / 0** |
| Truncated review with lost claims | — | yes | no lost claims |

This six-row result is a slow-tail check, not the final full configuration. The
final full run selected 16,384 after the 8,192 variants were measured.

### Full 140-question rerun

Four full configurations were recorded after the review changes:

| 140 questions | Baseline | 8,192, no escalation | 8,192 + length escalation | 16,384 pre-coverage | 16,384 + coverage-skip |
|---|---:|---:|---:|---:|---:|
| Model-backed P50 | 237.7 s | 191.0 s | 187.0 s | 155.1 s | **121.3 s** |
| Model-backed P95 | 842.1 s | 618.3 s | 682.5 s | 586.7 s | **464.6 s** |
| Answerable answered | 97 / 100 | 98 / 100 | 96 / 100 | 96 / 100 | **98 / 100** |
| Claims | 407 | 400 | 405 | 406 | **394** |
| Unsupported / numeric errors | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | **0 / 0** |
| Review `finish_reason=length` rows | — | 6 | 5 | 0 | **2** |
| One-claim negation answers | 0 | 2 | 0 | 0 | **2** |

The 8,192-only run is faster and answers one more row, but it silently drops
four claims from each of two negation answers. The 8,192-plus-escalation run
prevents the one-claim truncation but is slower and has lower coverage. The
16,384 run has no review truncation, 406 claims (one below the baseline), and
good P50/P95. The final coverage-skip run skips answer-coverage review when a
repair is already pending; it reaches **98/100 and P50 121.3 s / P95 464.6 s**,
but claims fall to 394 and two negation answers are one claim each. That is a
real speed/coverage trade-off, not a free win.

The two remaining partials are `negation-013` and `negation-018`; two other
negation rows are intentionally short. They are answer-shaping/review outcomes,
not retrieval or token truncation. Do not use the 8,192-only 618.3 s P95 or the
six-row 349.6 s result as the product number.

## Current status

Landed: a bounded review attempt cap, one verifier retry, compact scalar verdict
fields, one review-driven repair, coverage review skipped on non-final repair
passes, request IDs and an optional access log, plus the evidence-preview UI and
backend event. The final coverage-skip full run is P50 121.3 s / P95 464.6 s,
98/100 answerable rows, 394 claims, zero unsupported claims, two review `length`
turns and two intentionally short negation answers. Remaining work is independent
human review and a repair-policy pass for the two partials (`negation-013/018`).
Do not present the six-row 349.6 s check as a full-population P50/P95.

## What this optimization will not do

- It will not parallelize tool calls or add a cross-question retrieval cache as
  a latency fix. The measured non-model residual is P50 1.5 s / P95 6.0 s; the
  serial tool/prefetch path cannot move a 237 s median or an 842 s tail. The
  ledger and submission-order risks are larger than the gain.
- It will not reduce retrieval depth or drop cited evidence to hit a timing
  target. Retrieval is tens of milliseconds; wrong citations are the product.
- It will not route planning or extraction to the review model. Planning is
  already deterministic, and using the reviewer as an author would empty the
  independent-review candidate set.
- It will not batch more claims per review to reduce call count. A larger batch
  increases output length and the chance of crossing the timeout or truncation
  boundary.
- It will not semantic-cache final answers. A cached answer would need the
  current filing, parser, prompt and verifier versions in its key and a fresh
  deterministic recheck before display.
- It will not remove the deterministic numeric/quote verifier or let a model
  reviewer approve a claim whose evidence is missing.
- It will not hide a timeout. A withheld claim or explicit gap is preferable to
  a fast but unsupported answer.

## External references

The design is consistent with published practice:

- [Databricks/Mosaic inference serving guide](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices):
  output length dominates decode latency; a few hundred extra output tokens can
  cost more than a much larger prompt.
- [Anthropic multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system):
  bounded parallel tool calls and subagents reduced research time substantially;
  synchronous joins remain the bottleneck.
- [NN/g response-time limits](https://www.nngroup.com/articles/response-times-3-important-limits/):
  show evidence within a second or two, and keep progress visible when the full
  answer takes longer.
- [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai):
  record per-stage spans, TTFT, total duration, rounds, tool calls and cache
  hits rather than a single end-to-end number.
- [vLLM automatic prefix caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/):
  repeated questions over the same long filing benefit from a stable immutable
  prefix; caching does not reduce the decode time of new output.

The practical conclusion for this project is narrow: shorten and cap the review
output, remove redundant review retries, stop after one repair, and expose
evidence before the final synthesis. Retrieval tuning and answer caching are
not the first-order fixes.
