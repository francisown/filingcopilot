# Roadmap

The current workspace supports filing research, financial calculations, price
charts, company comparison and saved research packages. Coverage varies by issuer
and market.

## Priorities

1. Improve answer completeness and make each remaining evidence gap specific.
2. Address candidate-pool misses and ranking misses on the bilingual retrieval set.
3. Expand segment, guidance, transcript and historical-quarter coverage.
4. Complete cross-currency share-class reconciliation where dated source inputs exist.
5. Reduce orchestration complexity and improve setup across supported platforms.

## Evaluation

Complete independent human review of full answers, with separate measurements for
numeric accuracy, citation support, completeness, latency and model use. The
[evaluation page](docs/evaluation.md) lists existing experiments and review scope.

## Planned extensions

- Deterministic company screening.
- Watchlist disclosure monitoring (Monitor v1: 10-K/10-Q/8-K facts diff, 8-K
  item classification, evidence brief, case review and offline export).
- Authentication and operational checks for hosted deployment.

These extensions are not included in the current local application.
