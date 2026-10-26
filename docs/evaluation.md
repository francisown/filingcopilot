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
and the separate follow-up checks. The 18 partial rows are classified in the
[partial-answer triage](validation/partial-triage-2026-10-26.md): 3 calculation,
5 evidence-missing, 5 timeout/model, 4 correct refusal/scope limitation, and 1
review-independence issue.

**20-question live check (2026-10-26, frozen `f2f12f4`):** 20 stratified
questions (4 each: numeric, causality, risk, negation, cross-period) were run
with a fresh copy of the 62-filing corpus, `mimo-v2.6-flash` /
`glm-5.3-flash`, and the 300s budget with 30s review reserve. It recorded
**11 answered, 9 partial, 0 abstained**, coverage **0.55**, P50/P95
**47.8s / 152.1s**, numeric mismatch **0**, citation binding **1.0**, and no
timeout/runner errors. The same 20 IDs in the historical run were **17
answered / 3 partial**, so the complete-rate acceptance failed despite the
latency target passing. The full checkpoint, per-question results and request
log are in the [frozen 20-question record](validation/live-20-2026-10-26/);
the full 140-question run is intentionally not started.

A model-control run on the same 20 questions compared `deepseek-v4.1-flash`
with `mimo-v2.6-flash` under the same review model, corpus, budget and tools.
DeepSeek recorded **11 answered / 9 partial**, **7.85 tool calls/question**,
**0 direct-text rounds**, P95 **147.8s**; Mimo recorded **15/5**, **6.55**
tool calls, **0 direct-text rounds**, P95 **250.3s** in that run, while an
earlier Mimo run on the same questions was **11/9**. The trace does not support
the “DeepSeek does not call tools” or “answers directly” hypotheses, and the
Mimo run variance is large. See the
[model-control record](validation/model-control-2026-10-26/README.md). Older measurements below remain historical.
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
See the current repository history for later fixes.

## Retrieval

The frozen set contains 200 bilingual questions across five companies, with exact
passage IDs. These are corpus-anchored retrieval measurements.

| Configuration | Recall@5 | MRR@5 |
|---|---:|---:|
| Full query, 25 candidates (historical) | 0.155 | 0.084333 |
| Focused query, 75 candidates, anchor weight 0.05 (historical) | 0.260 | 0.150333 |
| Focused query, 75 candidates, fused title/period/coverage (2026-10-26) | **0.355** | **0.21975** |

The fused 2026-10-26 local rerun used the same 200-question gold set, 75
candidates per leg, a bounded 10,000-passage coverage scan and embedding mode
off. Candidate hit rate was 94%; P50/P95 was 84.8/165.0 ms. Recall@5 ≥ 0.35 was
met and MRR improved from the 0.150 historical focused result. The result is
[frozen here](validation/retrieval-fusion-2026-10-26.json); the 22-question
FinanceBench held-out set was not re-run in this offline pass. Cross-encoder and multilingual
candidates failed the combined recall/MRR/latency gate and remain disabled. The
negative result is retained in the local evaluation archive.

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

**Scoped FinanceBench check (2026-10-26 support re-run):** evidence-page
benchmark: 150 questions over 163 evidence pages, company+year scope,
section-aware 3,000-character chunks with a 4,000-character parent window and
title-based top1 rerank: **page hit@5 0.920, page@1 0.687, snippet hit@5 0.920,
answer full/partial/unsupported top1 0.207/0.133/0.660 raw; page-level
0.213/0.147/0.640; on 42 full-evaluable rows 0.738 top1 / 0.929 at5; MRR@5
0.784**. The earlier 2026-10-23 any-one-number answer figures (0.427 / 0.913)
are retired. The full-filing benchmark covers 61 parsed PDFs and 113 questions
with a BGE ONNX reranker over the top5 router pages: **page hit@5 0.850,
page@1 0.540, snippet hit@5 0.858, snippet hit@1 0.531, MRR@5 0.664**.
The strict support classification did not improve these full-filing metrics;
they are recorded retrieval measurements, not a quality improvement. A
2026-10-26 full-filing rerun recorded page@1 **0.540**, page@5 **0.850**,
snippet@5 **0.858**, MRR **0.664**, and strict answer
full/partial/unsupported top1 **0.221 / 0.186 / 0.593**; at5
**0.319 / 0.248 / 0.434**. The page/snippet result is unchanged within noise;
the support result is diagnostic, not a claimed quality gain. See the
[frozen record](validation/financebench-full-2026-10-26.json). BGE adds
about 1.8 s P95 on CPU. BGE's 22-question 60→80 document extension reaches page
hit@1 0.545, page hit@5 0.773 and MRR 0.621; it is still small and
document-selected. Full report:
[financebench-scoped-2026-10-23.md](financebench-scoped-2026-10-23.md) and
[support result](validation/financebench-support-2026-10-26.json).

**Answer-support definition (2026-10-26):** evidence is classified as
**full / partial / unsupported**. Full requires every key number, unit and
period in the evidence set; a verified calculation relation may support a
result that is not copied verbatim, provided its input values are present.
Partial means some required values are present; unsupported means none is.
Numeric substring matches do not count. The earlier implementation accepted any
one matching number and therefore overstated support; 795/1,085 FinDER
reasoning rows had only a partial numeric match. Full is not the same as partial
and the old any-number values are not comparable.

**FinDER answer-support (2026-10-26 support re-run):** 1,085 `reasoning=True`
rows, company-scoped reference corpus with category fallback, BM25 + FlashRank.
The default retriever scopes references by company when the question contains a
ticker or company alias, and falls back to the FinDER category otherwise.
Company metadata is inferred from reference headers and question text, never
from the answer column; 600/1,085 questions are company-scoped and 382 company
groups are built. This is metadata filtering over reference excerpts, not
full-filing page retrieval. Full/partial/unsupported top1 is
**0.098/0.666/0.236**; at5 is **0.160/0.700/0.140**. On the 259 full-evaluable
rows, full top1/at5 is **0.409 / 0.637**. Reference hit@5 is **0.598** and P95
is **271 ms**. Script: `scripts/evaluate_finder.py`; result:
[support result](validation/finder-support-2026-10-26.json).

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
| FinanceBench evidence-page page@1 | 0.687 | 150 | 0.609–0.755 |
| FinanceBench evidence-page page@5 | 0.920 | 150 | 0.865–0.954 |
| FinanceBench full answer top1 | 0.207 | 150 | 0.150–0.278 |
| FinanceBench partial answer top1 | 0.133 | 150 | 0.088–0.197 |
| FinanceBench unsupported answer top1 | 0.660 | 150 | 0.581–0.731 |
| FinanceBench full answer at5 | 0.287 | 150 | 0.220–0.364 |
| FinanceBench partial answer at5 | 0.180 | 150 | 0.127–0.249 |
| FinanceBench unsupported answer at5 | 0.533 | 150 | 0.454–0.611 |
| FinDER full answer top1 | 0.098 | 1,085 | 0.081–0.117 |
| FinDER partial answer top1 | 0.666 | 1,085 | 0.638–0.694 |
| FinDER unsupported answer top1 | 0.236 | 1,085 | 0.212–0.262 |
| FinDER full answer at5 | 0.160 | 1,085 | 0.140–0.183 |
| FinDER partial answer at5 | 0.700 | 1,085 | 0.672–0.726 |
| FinDER unsupported answer at5 | 0.140 | 1,085 | 0.121–0.162 |

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
distinct from every author model. The affected historical records remain in the
local archive.

## Reproduction and interpretation

Use [test profiles](test-profiles.md) for regression commands and
Checkpoints bind
questions, source hashes, model configuration and budgets; a mismatched resume is
rejected. Compare runs with their cache conditions and denominators intact.

Package audits check the requested structure, arithmetic and source comparisons.
They do not reassess narrative meaning or investment performance. Missing source
files, ADS ratios, dated FX rates and original numeric inputs remain explicit.
