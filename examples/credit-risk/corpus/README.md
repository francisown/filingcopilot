# Synthetic credit-risk corpus

These documents are fabricated for a retrieval and evidence-verification demo. They do not describe a real bank, a real model, or real customers. Do not use them for lending, validation, or regulatory decisions.

The corpus mirrors the document types a credit-risk team actually works with:

- `credit-policy-v3.md` — policy limits, exception approvals, income verification, revalidation triggers.
- `model-validation-report-2026.md` — SR 11-7-style independent validation findings, limitations, and required actions.
- `portfolio-performance-2026Q2.md` — segment delinquency and coverage reporting.

The demo ingests these files into the same SQLite FTS5 evidence store used by FilingCopilot, retrieves cited passages, exports a frozen evidence package, and verifies it offline.
