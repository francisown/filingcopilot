# Latency and cost budget

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

## External patterns

- Anthropic's agent guidance recommends simple workflows, **routing** and
  **parallelization** rather than adding more autonomous rounds by default.
- GPTCache-style semantic caching helps repeated/similar questions, but this
  evaluation has mostly unique questions; the expected hit rate is low and a
  false hit on a financial question is expensive.
- LLMLingua-style prompt compression can reduce tokens, but compressing filing
  evidence risks dropping the exact number, period or qualifier that the
  evidence checks depend on; it is not enabled without a paired accuracy test.

## Change applied

`FC_REVIEW_BATCH_SIZE` / `Settings.review_batch_size` now defaults to **4**
instead of 2. A 9-claim risk answer goes from five serial review calls to three;
a 4-claim answer goes from two to one. The reviewer still returns one verdict per
claim, and the existing batch-validation/withholding tests remain in force.

This is a call-count optimization, not a latency claim. The live harness was not
available in this environment, so P95 has **not** been re-measured after the
change.

## Candidate changes still unvalidated

1. Parallel review batches (subject to provider rate limits).
2. Smaller extraction/review models and a fast route for simple numeric questions.
3. Parallel independent tool calls.
4. A hard token budget in addition to the wall-clock budget.
5. Bounded partial answers with explicit gaps when the budget expires.

## Status

**Partially applied.** Review batching is changed and unit-tested; live P95 is
unresolved. The README continues to report the 417.1s result rather than hiding
it.
