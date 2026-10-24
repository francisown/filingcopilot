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

## Candidate changes

1. Cache deterministic retrieval and repeated model prompts within a run.
2. Use smaller extraction/review models and reserve the large model for final
   synthesis.
3. Parallelize independent tool calls.
4. Enforce a hard token budget in addition to the existing wall-clock budget.
5. Return a bounded partial answer with explicit gaps when the budget expires.

## Status

**Unresolved.** None of these changes has been validated with a live model
harness. Adding a smaller default without a paired quality measurement would be
an unverified regression. The current README reports the 417s result rather than
hiding it.
