# Show HN draft

## Title options

1. Show HN: FilingCopilot – evidence-grounded research for SEC filings
2. Show HN: An agent that cites filings and exports a verifiable evidence package
3. Show HN: RAG for company filings, with an offline verifier

## Body

I built FilingCopilot because most financial RAG demos answer the question but cannot show the evidence.

It is a local-first Python/FastAPI workspace for SEC filings. A LangGraph agent retrieves passages, calls financial tools, and drafts claims. Every claim goes through deterministic numeric checks and a semantic review before it is shown. The answer, citations, calculations, source hashes, and a package hash are exported together. A standalone verifier re-checks the package offline — no agent, no model, no network.

The deterministic path answers annual numbers, growth, margins, and trends without an LLM at all. The agent path is for explanations and narrative questions.

Recorded evaluation: 140 bilingual questions; 81/100 research questions complete, 18 partial, 1 no answer; 116 exact SEC/arithmetic matches with 0 mismatches. Honest limitations: self-reviewed, no independent human gold standard, retrieval Recall@5 0.430 after the coverage rerank (0.255 lexical baseline), model-using questions P95 417s, 4.3M tokens.

The repo also contains a synthetic credit-risk vertical demo: policy clauses, model-validation findings, and portfolio tables, using the same package and verifier.

I would appreciate feedback on three things: the evidence model, the evaluation design, and whether the verifier is useful to someone outside the project.

## First comment

Architecture and caveats:

- Coverage retrieval: SQLite FTS5, bilingual query expansion, IDF-weighted term coverage and a small section prior; a 200-question gold set with Recall@5 0.430 (0.255 lexical baseline).
- Deterministic gates: numeric recomputation, currency direction, document-scope overreach, completeness.
- Semantic review: subject, direction, period, materiality, evidence scope. Model judgment, not certification.
- Frozen package: canonical JSON + SHA256 manifest; verifier checks package hash, document hash, and quote span.
- Tests: 1,718 passing on the current branch (1,656 at the recorded source).
- What it is not: not investment advice, not a production credit-decision system, not a certified model.

Happy to answer questions about the failure taxonomy or the credit-risk mapping.
