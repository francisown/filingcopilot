# Evaluation

Evaluation separates workflow delivery, financial calculations, retrieval and
answer-content review. The results below belong to their recorded source and
configuration snapshots.

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

The internal exact-passage gold remains a regression set. It measures strict
paragraph identity, not human relevance; the product-scoped external check below
is the number to report.

## External datasets

The internal gold set is corpus-anchored and author-reviewed. For an independent
view, see [external evaluation datasets](../evaluation/external/README.md):

- **FinanceBench** (`PatronusAI/financebench`, CC-BY-NC-4.0): 150 public SEC
  filing questions with answers and evidence pages. Closest to the product;
  non-commercial; not a full-filing retrieval corpus.
- **DocFinQA** (`kensho/DocFinQA`, MIT): 7,437 long-context 10-K/10-Q questions;
  the test split is 922 rows. No evidence span.
- **FiQA** (`BeIR/fiqa` + `BeIR/fiqa-qrels`, CC-BY-SA-4.0): 57,638 passages,
  6,648 queries, 17,110 qrels. Standard financial retrieval; report nDCG@10 and
  Recall@100, not exact-passage R@5.

`scripts/fetch_financebench.py` writes a local JSONL copy plus a license and
SHA256 manifest under the gitignored `evaluation/external/data/` directory.

**Scoped FinanceBench check (2026-10-23):** evidence-page benchmark: 150
questions over 163 evidence pages, company+year scope, section-aware
3,000-character chunks with a 4,000-character parent window and title-based
top1 rerank: **page hit@5 0.920, page@1 0.707, snippet hit@5 0.920,
answer-supported top1 0.427, MRR@5 0.798**. The
full-filing benchmark covers 61 parsed PDFs and 113 questions with a BGE
ONNX reranker over the top5 router pages: **page hit@5 0.850, page@1 0.540,
snippet hit@5 0.858, snippet hit@1 0.531, MRR@5 0.664**. BGE adds about 1.8 s
P95 on CPU. The earlier FlashRank baseline was page@1 0.478 / page@5 0.850 /
MRR 0.624. BGE's 22-question 60→80 document extension reaches page hit@1 0.545,
page hit@5 0.773 and MRR 0.621; it is still small and document-selected. The
earlier global-corpus number (0.507/0.407) was retired because it ignored the
product's selected-company scope. Full report:
[financebench-scoped-2026-10-23.md](financebench-scoped-2026-10-23.md).

**FinDER answer-support (2026-10-24):** FinanceBench raw answer support is
ceiling-limited: only 69/150 answers appear in the gold evidence page (49/50
`metrics-generated` answers are computed), so its raw 0.427 is not a fair
retrieval headline. On that evaluable subset, top1 is 0.913 and at5 is 1.000.
For a cleaner answer-support benchmark we added FinDER (CC-BY-NC-4.0): 1,085
`reasoning=True` rows. The default retriever scopes references by company when
the question contains a ticker or company alias, and falls back to the FinDER
category otherwise. Company metadata is inferred from reference headers and
question text, never from the answer column; 600/1,085 questions are
company-scoped and 382 company groups are built. This is metadata filtering over
reference excerpts, not full-filing page retrieval.

Company scope + BM25 + FlashRank gives answer top1 **0.823** / at5 **0.893**;
on the 1,039 rows whose answer is present in a gold reference, **0.857 / 0.928**.
Reference hit@5 is **0.598** and P95 is **287 ms**. The category-only baseline
is 0.794 / 0.876 with reference hit@5 0.371 and P95 309 ms; BM25-only company
scope is 0.757 / 0.892. Answer support remains the primary metric because the
benchmark still does not provide full-filing page scope. Script:
`scripts/evaluate_finder.py`; result: `artifacts/finder-eval.json`.

### Sampling uncertainty

These are finite samples. The 95% Wilson intervals below are descriptive, not a
correction for document selection, tuning on the same questions, or clustering
by company/document.

| Metric | Estimate | n | 95% Wilson interval |
|---|---:|---:|---:|
| FinanceBench full-filing page@1 | 0.540 | 113 | 0.448–0.629 |
| FinanceBench full-filing page@5 | 0.850 | 113 | 0.772–0.904 |
| FinanceBench full-filing snippet@1 | 0.531 | 113 | 0.439–0.620 |
| FinanceBench full-filing snippet@5 | 0.858 | 113 | 0.782–0.911 |
| Full-filing 22-question extension page@1 | 0.545 | 22 | 0.347–0.731 |
| Full-filing 22-question extension page@5 | 0.773 | 22 | 0.566–0.899 |
| FinanceBench evidence-page page@1 | 0.707 | 150 | 0.629–0.774 |
| FinanceBench evidence-page page@5 | 0.920 | 150 | 0.865–0.954 |
| FinanceBench evaluable answer top1 | 0.913 | 69 | 0.823–0.960 |
| FinDER answer top1 | 0.823 | 1,085 | 0.799–0.845 |
| FinDER answer at5 | 0.893 | 1,085 | 0.873–0.910 |
| FinDER evaluable answer top1 | 0.857 | 1,039 | 0.834–0.877 |
| FinDER evaluable answer at5 | 0.928 | 1,039 | 0.911–0.942 |

The 22-question extension is especially noisy: its page@5 interval spans
0.57–0.90. None of these intervals addresses human relevance or correctness.

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
