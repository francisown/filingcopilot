# Latency and cost budget

**2026-10-26 default:** `FC_RESEARCH_TIMEOUT=300` with
`FC_REVIEW_RESERVE_SECONDS=30`. New research rounds stop when only the final
review reserve remains. The historical 417s run below predates this change and
has not yet been re-run on the public API.

## Measured problem

The recorded 140-question run used models for 60 questions:

- Median latency: 92.79s
- P95 latency: 417.11s
- Reported tokens: 4,312,447
- Model HTTP requests: 476
- Research budget timeouts: 3

A 417s P95 is not acceptable for interactive research. Retrieval itself is much
faster (FinanceBench evidence-page P95 ~8ms; FinDER ~287ms; full-filing BGE
rerank ~1.84s), so this is a model/tool-orchestration budget problem, not a
retrieval-speed problem.

## Target

- P95 under 30s for a focused question.
- A hard per-request token/cost ceiling, reported in the answer metadata.
- Partial answers and refusal within the budget, never silent truncation.

## What the run profile says

- 60 model-backed questions; median 92.79s, P95 417.1s.
- Five questions exceeded 300s; three hit the 420s research budget.
- Risk questions dominate the slow tail; the 9-claim cases were reviewed in
  batches of two, serially.
- Median 8 and P95 13 HTTP requests per model-backed question.

This is an orchestration problem, not a retrieval problem. The full-filing
retrieval path is 1.84s P95; FinDER retrieval is 287ms P95.

A direct provider probe used the same `mimo-v2.6-flash` model, endpoint and
`x-opencode-session` header as the gateway, with no project tools or agent:

| Prompt | Prompt tokens | Completion tokens | Wall time |
|---|---:|---:|---:|
| Short risk prompt | 98 | 512 (`length`) | 20.3s / 20.9s |
| 4,271-token risk context | 4,271 | 665 / 746 (`stop`) | 22.4s / 21.3s |

The provider therefore has a **roughly 20-second floor per call** even when the
input is tiny; generation dominates and context size is secondary. The project
makes 8–12 such calls for a slow question, so a 160–260s floor is expected
before retries, serial review and queueing. That does not excuse 417s: it means
the fix must reduce/parallelize calls, not only optimize retrieval.

## Model swap tested and rejected: deepseek-v4.1-flash

A direct probe of `deepseek-v4.1-flash` on the same endpoint was faster than
`mimo-v2.6-flash`:

| Model | Short wall | 4k-context wall |
|---|---:|---:|
| `mimo-v2.6-flash` | 20.3 / 20.9s | 22.4 / 21.3s |
| `deepseek-v4.1-flash` | 16.3 / 4.7s | 7.2 / 4.4s |

A first five-question risk subset looked promising: P50 420.0s→88.5s, P95
420.8s→117.0s, coverage 0.2→0.6. The full 140-question run then exposed a
quality regression:

| Metric | mimo historical | deepseek full run |
|---|---:|---:|
| Answered | **81/140** | 64/140 |
| Partial | 18/140 | 36/140 |
| Abstained | 41/140 | 40/140 |
| Coverage | **0.579** | 0.457 |
| Model-backed P95 | 417.1s | **139.0s** |
| Negation answered | **16/20** | 4/20 |
| Risk answered | **11/20** | 7/20 |
| Causality answered | 14/20 | 13/20 |

The speed gain is real, but it is a **quality-for-speed trade-off**, especially
on negation and risk. It was not adopted; the local `.env` is back to
`mimo-v2.6-flash` with `glm-5.3-flash` review, batch 2, max_rounds 8. A future
routing design could send only categories that retain quality to DeepSeek, but
that requires a held-out per-category quality gate.

Two prompt variants were tested after the no-prompt run:

| Variant | Answered | Risk | Negation | Causality | P95 |
|---|---:|---:|---:|---:|---:|
| DeepSeek, no added guidance | 64/140 | 7/20 | 4/20 | 13/20 | 139.0s |
| + negation/risk gap guidance | **76/140** | **16/20** | 10/20 | 10/20 | 129.4s |
| + causality gap guidance | 67/140 | — | — | — | 174.3s |

The first prompt was a real improvement but still below mimo's 81/140; the
causality-specific variant regressed. Prompt text alone is not enough to close
the gap. The experiment code was reverted; only this result is retained.

## External patterns

- Anthropic's agent guidance recommends simple workflows, **routing** and
  **parallelization** rather than adding more autonomous rounds by default.
- GPTCache-style semantic caching helps repeated/similar questions, but this
  evaluation has mostly unique questions; the expected hit rate is low and a
  false hit on a financial question is expensive.
- LLMLingua-style prompt compression can reduce tokens, but compressing filing
  evidence risks dropping the exact number, period or qualifier that the
  evidence checks depend on; it is not enabled without a paired accuracy test.

## Live A/B: review batch 2 vs 4

Two slow risk questions from the live set were rerun with the same model,
corpus and `workers=1`; only `FC_REVIEW_BATCH_SIZE` changed. Each arm is one
sample, so this is directional, not a P95 estimate.

| Question | Batch | Latency | Status | Coverage |
|---|---:|---:|---|---:|
| NVIDIA FY2025 risk (`semantic-risk-020`) | 2 | 214.9s | partial | 0.0 |
| NVIDIA FY2025 risk | 4 | **157.1s** | **answered** | **1.0** |
| Apple FY2025 risk (`semantic-risk-012`) | 2 | **304.1s** | partial | 0.0 |
| Apple FY2025 risk | 4 | 421.5s | **abstained / timeout** | 0.0 |

The larger batch helped one question and made another hit the 420s deadline.
It is therefore **not a stable latency optimization**. `Settings.review_batch_size`
remains **2**; the code comment records the failed experiment. The `.env`
already overrode the code default with 2, which is why the earlier default-only
change did not affect real runs.

The reviewer still returns one verdict per claim, and the batch-validation /
withholding tests remain in force.

## Live route rejected: risk questions with six rounds

The next experiment kept batch=2 and limited risk questions to six reasoning
rounds. The first two slow questions improved, so the same configuration was
run on the five slowest risk questions from the live set. The result did **not**
hold:

| Question | max8 latency/status | risk6 latency/status |
|---|---:|---:|
| `semantic-risk-001` | 420.0s partial | 388.4s partial |
| `semantic-risk-008` | 378.0s **answered** | 306.9s **partial** |
| `semantic-risk-010` | 417.1s partial | 116.7s partial |
| `semantic-risk-019` | 420.0s partial | 420.5s partial / timeout |
| `semantic-risk-020` | 420.8s abstained | 82.7s **answered** |

- P50 improved: 420.0s → 306.9s
- P95 did **not** improve: 420.8s → 420.5s
- Coverage stayed 0.2 (1/5); one timeout remained
- One question improved, one regressed from answered to partial

The six-round route is therefore **not a stable optimization** and was
reverted. It remains a documented failed experiment, not a default.

## Candidate changes still unvalidated

1. Parallel review batches (subject to provider rate limits).
2. Smaller extraction/review models and a fast route for simple numeric questions.
3. Parallel independent tool calls.
4. A hard token budget in addition to the wall-clock budget.
5. Bounded partial answers with explicit gaps when the budget expires.

## Status

**Not adopted.** Batch 4, risk-specific six-round routing, and the DeepSeek
model swap were all rejected. DeepSeek cut the full-run P95 from 417.1s to
139.0s, but answered fell from 81/140 to 64/140 and negation dropped from 16/20
to 4/20. The local `.env` is back to `mimo-v2.6-flash` + `glm-5.3-flash`, batch
2, max_rounds 8. The README continues to report the historical 417.1s result.
Next candidates are parallel review batches at batch 2 and category routing
with a held-out quality gate.
