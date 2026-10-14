# Offline annual-facts example

`annual-facts.json` was generated from the bundled SEC filing/CompanyFacts
snapshot, for AAPL FY2025 revenue and net income as of 2026-10-08, in reference
mode. Generation used zero model calls and blocked network requests.

```powershell
filingcopilot verify-package examples/annual-facts.json
```

The adjacent audit checks package structure, formulas and numeric text only.
`source_validation` is `not_requested` and raw-source checks were not requested:
the public sample does not include the developer's private corpus or raw caches.
It is a reproducible consistency example, not an issuer-authenticated report or
a human semantic-accuracy certificate. Original filing links remain in the package.

For caller-trusted source/corpus replay see the
[independent verifier](../docs/independent-verifier.md).
