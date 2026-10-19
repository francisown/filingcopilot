# Evaluation

Evaluation separates workflow delivery, financial calculations, retrieval and
answer-content review. The results below belong to their recorded source and
configuration snapshots.

## Source review of the full-run answers — 2026-10-19

The saved answers were subsequently compared with SEC filings, issuer annual
reports and explicit boundary criteria. Numeric references bind the reporting
period, currency, accounting basis and calculation inputs. Narrative references
identify required answer points and source locations. This was an official-source
check with model-assisted content review, not a blind test or independent expert
certification. The answers were not regenerated or changed for this review.

| Research question group | Core supported | Partially addressed | Wrong or unsupported | Core unanswered |
|---|---:|---:|---:|---:|
| Numeric | 20 | 0 | 0 | 0 |
| Annual comparisons | 20 | 0 | 0 | 0 |
| Causes | 17 | 2 | 0 | 1 |
| Risks | 16 | 1 | 1 | 2 |
| Negation checks | 9 | 4 | 7 | 0 |
| **Total** | **82** | **7** | **8** | **3** |

Of the 81 answers originally marked complete, 72 met the core reference criteria,
three were incomplete and six contained material errors or unsupported conclusions.
"Core supported" permits minor qualifications noted in the review; it does not
certify every incidental statement. All 40 boundary responses met their content
criteria, but they exercise only three policy templates, not 40 independent outage
scenarios. These findings demonstrate why application completion, source binding
and passing software tests are separate from answer correctness.

One error reversed the currency subject in Apple's FY2022 pricing risk: weaker
foreign currencies against the US dollar were described as a weaker dollar.
Another treated NVIDIA's FY2025 demand-decline disclosures as exclusively future
risks despite disclosures of experienced reductions. Other failures overstated
materiality, inferred demand from revenue, or replaced requested explanations with
figures. TSMC's Taiwan and SEC reports use different accounting bases; their net
income difference was not treated as a calculation error.

The [per-question source review](validation/source-review-2026-10-19.json) contains
reference points, source URLs and locations, judgments and original answer hashes.
The full-run status and usage measurements below remain historical observations.

After this review, the repair sequence used 15 real API questions selected from
known failures and controls on independent data copies. The first 12 exercised the
initial repair snapshot; a separate 3-question follow-up exercised the final
boundary changes. They are diagnostic examples rather than a new accuracy rate.
The current source tree passes 1,705 Python tests; the original full-run
measurements below were not rerun or replaced.

## Live evaluation — 2026-10-19

The latest full run used all 140 unchanged bilingual questions, a frozen copy of
`17a1d04`, four workers and the configured model API. The knowledge date was
2026-10-08. Data came from a separate copy of the existing corpus and warm source
caches: 62 filings, 1,166 financial facts, 39,912 statement facts and 31,123 passages.
This tests the fixed filing corpus, not current market prices or a fresh installation.
Complete and partial below are application status labels.

| Question group | Complete | Partial | No research answer |
|---|---:|---:|---:|
| Numeric (20) | 20 | 0 | 0 |
| Causes (20) | 14 | 6 | 0 |
| Negation checks (20) | 16 | 4 | 0 |
| Risks (20) | 11 | 8 | 1 |
| Annual comparisons (20) | 20 | 0 | 0 |
| **Research questions (100)** | **81** | **18** | **1** |

The other 40 questions ask about unsupported future-price guarantees or hypothetical
market-source failures. All received specific boundary explanations. The runner
records these service exceptions as `abstained`; they are separate from the one
research question that produced no answer. A separate check of the REST endpoint
confirmed all 40 responses, including English explanations for the 20 English
questions, with model calls disabled. No market outage was injected.

Of the 99 nonempty research answers, 57 contain narrative or analysis and 42 contain
only typed numbers. Two of those numeric-only answers were explanation/risk questions
whose model work did not finish. All 20 annual comparisons include a calculation
and reproducible period/source wording, with no model requests. These are delivery
and coverage observations, not an independent human accuracy score.

| Usage and checks | Result |
|---|---:|
| Questions using the model API | 60 |
| Model HTTP requests started | 476 |
| Recorded HTTP responses | 442, all HTTP 200 |
| Reported tokens | 4,312,447 |
| Median / P95 latency for model-using questions | 92.79 / 417.11 seconds |
| Research budget timeouts | 3 |
| Runs explicitly marked as lacking independent review | 3 |
| Exact SEC CompanyFacts / arithmetic matches | 116 |
| Numeric claims outside that checker's scope | 34 |
| Saved packages audited against available raw sources | 100 consistent, none missing sources |

The 80 questions without model requests make the overall median 0.075 seconds;
that figure does not describe model research latency. Requests without a recorded
HTTP response and unreported usage remain unaccounted for in the token total.
Dollar cost is unknown because model prices and a complete invoice were unavailable.

This run exposed three engineering issues. String-encoded integer tool arguments
could cause retrieval errors; that fix was included in the full run. Incomplete
English fallback messages and repeated copies of the same financial observation
were fixed afterward and checked separately without additional model generation.
The latter change preserves different underlying values, periods, currencies and
explicit source/revision requests. The original full-run answers remain unchanged.
The original full-run source passed 1,656 Python tests and 36 frontend checks. All 100 saved
packages also replayed consistently under that source, without changing their
answer or package hashes; the duplicate-fact repair was separately replayed from
the captured failing case with no model calls.
An earlier attempt stopped after 29 questions is excluded; it recorded 69 model
HTTP requests in completed questions, with in-flight usage unknown.

The [machine-readable record](validation/live-evaluation-2026-10-19.json) includes
configuration, source fingerprints, per-question answers and gaps, audit results,
and the separate follow-up checks. Older measurements below remain historical.
This was a single current-source run, not a simultaneous rerun of the old GitHub
baseline. Model review can still accept overly broad wording, particularly claims
about what an entire filing does not say based on selected excerpts; independent
semantic review remains necessary.

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
