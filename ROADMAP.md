# Roadmap

FilingCopilot is a workspace for researching public companies. This page describes current
scope and priorities; planned items are not available features.

## Available today

- Company and listing search, long-term price charts, and an optional independent daily-price
  fallback when the operator has the required provider access.
- SEC annual and quarterly facts, trailing financial flows, financial trends, and supported
  foreign-issuer filings. Coverage varies by company and market.
- Company comparisons, editable valuation scenarios, and a six-part company research workflow.
- Original-source citations, saved research packages, and independent offline arithmetic and
  source-consistency checks.
- English by default, optional Chinese, and browser, CLI, REST, and MCP interfaces.

## Priorities

1. Improve the completeness of company research while preserving source and period checks.
   A partial answer should identify the missing evidence and a useful next step.
2. Improve bilingual retrieval on frozen examples. The evaluated neural reranking candidates
   remain disabled because they did not meet the combined quality and latency criteria.
3. Broaden financial and disclosure coverage, especially segments, guidance, transcripts,
   historical quarters, and cross-currency share-class reconciliation.
4. Strengthen provider failure recovery and make first-time setup easier across supported
   operating systems.

## Evaluation and extensions

Human semantic annotations are still incomplete. Automated source binding and regression
tests do not establish the accuracy of a financial interpretation. See
[evaluation scope](docs/evaluation.md) for the distinction.

Deterministic screening, automated watchlist disclosure monitoring, and optional deployment
authentication are planned extensions. They have not been advertised as completed features,
and the outstanding human review step has not been bypassed.

