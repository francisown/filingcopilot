# FinanceBench external check — 2026-10-23

**Scope:** 150 public FinanceBench rows, evidence-page retrieval. This is **not** full-filing retrieval: the public subset exposes the evidence pages, not every page of every 10-K/10-Q. The result is a useful external check, not a replacement for the internal 200-question gold set.

## Setup

- Data: `PatronusAI/financebench` public subset, 150 rows, CC-BY-NC-4.0.
- Questions: 50 `metrics-generated`, 50 `domain-relevant`, 50 `novel-generated`.
- Documents: 163 unique evidence pages from 10-K, 10-Q, 8-K and earnings documents.
- Corpus: one passage per evidence page, or 2,000-character chunks with 120-character overlap.
- Retriever: FilingCopilot coverage rerank, `embedding_mode="off"`; no model, no network.
- Metrics: page hit@k, evidence-snippet hit@k, MRR@5, answer-token support in top-1.
- Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_financebench.py `
  --mode coverage --chunk-size 2000 `
  --home .financebench-eval-2000 `
  --output artifacts/financebench-eval-chunk2000.json
```

## Results

### Page-level retrieval (163 pages)

| Path | Page hit@1 | Page hit@3 | Page hit@5 | MRR@5 | Answer support@1 | p95 |
|---|---:|---:|---:|---:|---:|---:|
| Coverage | 0.187 | 0.387 | **0.507** | 0.303 | 0.400 | 47 ms |
| Anchor | 0.207 | 0.373 | 0.473 | 0.304 | 0.393 | 12 ms |

Coverage finds more pages in the top 5 (+3.3 points) but is about 4× slower; MRR is essentially unchanged. The anchor path remains a reasonable latency-sensitive fallback.

### Chunked retrieval (2,000-character chunks, 296 passages)

| Metric | Result |
|---|---:|
| Page hit@1 / @3 / @5 | 0.287 / 0.447 / **0.540** |
| Snippet hit@1 / @3 / @5 | 0.167 / 0.320 / **0.407** |
| MRR@5 | **0.381** |
| Answer support@1 | 0.313 |
| p50 / p95 | 40 ms / 78 ms |

Chunking improves ranking (MRR 0.303 → 0.381) but the snippet-level hit rate is still only **0.407**. Page hit is inflated by multiple chunks per page; the snippet number is the meaningful one.

### By question type (chunk 2000)

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| domain-relevant | 50 | 0.500 | 0.380 |
| metrics-generated | 50 | 0.400 | **0.300** |
| novel-generated | 50 | 0.720 | 0.540 |

`metrics-generated` is the bottleneck. Questions use analyst language such as “capital expenditure”, while the page uses table language such as “Purchases of property, plant and equipment (PP&E)”. Lexical retrieval cannot bridge that gap reliably.

### By document type (chunk 2000)

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| 10-K | 112 | 0.482 | 0.348 |
| 10-Q | 15 | 0.667 | 0.600 |
| 8-K | 9 | 1.000 | 0.556 |
| Earnings | 14 | 0.571 | 0.571 |

## Interpretation

- **This is not a good product number.** A 0.407 snippet hit@5 means the exact evidence passage is missing from the top 5 for about six of ten questions. A production financial research tool needs better.
- **It is a different task from the internal gold.** FinanceBench questions are not written from page anchor terms; they are analyst-style questions over tables. The internal 0.430 exact-passage number and the external 0.507 page-hit number are not directly comparable.
- **The external result confirms the lexical ceiling.** The remaining gap is semantic/table-aware retrieval: synonyms, statement-type routing, and embeddings, not another global reranker.
- **Anchor is a useful latency fallback.** It has similar MRR at ~12 ms p95; coverage buys page hit at ~4× latency.
- **`answer_supported_top1` is not accuracy.** It only checks whether the gold answer token/number appears in the top passage. Do not report it as an answer-accuracy score.

## Limitations

- 150 public rows, not the full FinanceBench set; evidence pages only, not full filings.
- No negative pages; the corpus is small and all pages are evidence pages for some question.
- Chunk size was compared on the same 150 rows (800/1200/2000); 2,000 was best for snippet/MRR, so the number is development-tuned.
- CC-BY-NC-4.0: non-commercial; the data is gitignored and not redistributed.
- No model answer was generated. The next step is a 50-question answer + citation-support evaluation, which requires an API budget.

## Next step

1. Build a 50-question FinanceBench answer evaluation: retrieve top-k, ask the model to answer with citations, score answer correctness and citation support.
2. Add a table/statement router and a financial synonym layer for `metrics-generated` questions.
3. Re-run the internal 200-question gold and FinanceBench after each change; report both, never only the favorable one.
