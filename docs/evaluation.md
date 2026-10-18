# Evaluation

Evaluation separates workflow delivery, financial calculations, retrieval and
human citation judgments. The results below belong to their recorded source and
configuration snapshots.

## Historical model experiments

The 2026-10-11 production-budget run used 140 questions, a 240-second research budget
and a 60-second model-request budget. It recorded 40 answered, 67 partial and 33
abstained results. Of those, 37 met the frozen character minimum: 37/140, or 26.43%.
This measures workflow delivery and length, not human-rated completeness.

Evidence-ID binding was 75.91%; P50/P95 latency was 135.93/240.03 seconds. The source
snapshot was `a1881d4`, before later passage and indexing fixes. One cold-index case
took 369.55 seconds despite the nominal budget. Its failure is retained in the
[experiment records](../artifacts/README.md).

A later 140-question paired experiment against `43d9a98` used identical required
provider headers on both sides. Nonempty delivery rose from 97 to 129, while HTTP
requests rose from 609 to 707 and median latency from 59.32 to 83.03 seconds.
Further fixes followed that run; its figures are not current-release measurements.
See [recent changes](release-2026-10-18.md).

## Retrieval

The frozen set contains 200 bilingual questions across five companies, with exact
passage IDs. These are corpus-anchored retrieval measurements.

| Configuration | Recall@5 | MRR@5 |
|---|---:|---:|
| Full query, 25 candidates | 0.155 | 0.084333 |
| Focused query, 75 candidates, anchor weight 0.05 | 0.260 | 0.150333 |

In the focused-query diagnosis, 65 targets were absent from the candidate pool and
83 were present but ranked below the returned top five. The recall-doubling target
was not met. Cross-encoder and multilingual candidates failed the combined
recall/MRR/latency gate and remain disabled. The
[negative-result report](rerank-negative-result-2026-10-11.md) includes their setup
and reproduction commands.

## Human review

A first pass over 40 delivered narrative/analysis claims recorded 95% supported,
5% unclear and 0% unsupported. The project author was the sole reviewer and was not
a domain specialist. The sample was selected from delivered claims, so it excludes
rejected drafts and refusals. Independent domain review remains pending.

The original 20/40 worksheets and 872 paired-comparison units are preserved.
Scoring requires a human reviewer declaration and unchanged claim/evidence
fingerprints. Incomplete labels produce a separate subset report. Review miss-rate
calibration requires its matching decision key.

Independent-model calibration also checks the run trace: reviewer models must be
distinct from every author model. The
[2026-10-18 correction](reliability-followup-2026-10-18.md#review-calibration)
documents the affected historical records.

## Reproduction and interpretation

Use [test profiles](test-profiles.md) for regression commands and
[release checks](publication-checks.md) for distribution validation. Checkpoints bind
questions, source hashes, model configuration and budgets; a mismatched resume is
rejected. Compare runs with their cache conditions and denominators intact.

Package audits check the requested structure, arithmetic and source comparisons.
They do not reassess narrative meaning or investment performance. Missing source
files, ADS ratios, dated FX rates and original numeric inputs remain explicit.
