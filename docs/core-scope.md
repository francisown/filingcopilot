# Core scope and optional surfaces

The project currently contains more surfaces than one evidence problem needs.
This document makes the boundary explicit; it does not delete the optional
features.

## Core (must stay small and auditable)

1. SEC filing acquisition and local corpus.
2. Evidence retrieval with page/snippet locators.
3. Deterministic financial calculations and typed facts.
4. Claim verification and the frozen research package.
5. Offline package verification.

These are the only surfaces allowed to support the headline evidence claims.

## Optional / demonstration surfaces

- Market charts, peer comparison, valuation and DCF scenarios.
- Company workspace modules and long-form reports.
- The credit-risk vertical.
- The 30-tool MCP surface and every REST endpoint beyond research/evidence.
- Browser-only presentation and watchlists.

Optional surfaces may be useful, but they are not evidence of financial-answer
accuracy. They should not dilute the core latency or security budget. New
features should be removable without changing the core package format.

## What would be removed first

If the project were narrowed for production, the first candidates are the
market/valuation UI, watchlists, peer/comparison surfaces, and the credit-risk
demo. The core research service, evidence ledger, package format and verifier
should remain.
