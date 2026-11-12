# Roadmap

The current workspace supports filing research, financial calculations, price
charts, company comparison, saved research packages and disclosure monitoring
(Monitor v1: 10-K/10-Q/8-K facts diff, 8-K item classification, evidence brief,
case review and offline export). Coverage varies by issuer and market.

## Priorities

1. Reduce long-question latency with stage traces, fewer model rounds and a
   compact review path; report P50/P95 against the 30-second interactive target.
2. Complete an independent human review of full answers, with separate
   measurements for numeric accuracy, citation support, completeness, latency
   and model use. The [protocol](docs/human-evaluation-protocol.md) defines the
   sample and labels.
3. Improve answer completeness and make each remaining evidence gap specific.
4. Address candidate-pool misses and ranking misses on the bilingual retrieval set.
5. Expand segment, guidance, transcript and historical-quarter coverage.
6. Complete cross-currency share-class reconciliation where dated source inputs exist.
7. Improve setup across supported platforms and keep README, evaluation and CI
   numbers generated from the same source.

## Evaluation

The [evaluation page](docs/evaluation.md) lists existing experiments and review
scope. Its automated results are engineering measurements, not an independent
human accuracy study. Independent review remains pending.

## Planned extensions

- Deterministic company screening.
- Hosted-demo authentication, per-user budgets, request IDs, audit logging,
  tracing and rate limits. The current application is still a single-user local
  system and should not be published remotely without a TLS reverse proxy and
  these controls.

These extensions are not included in the current local application.
