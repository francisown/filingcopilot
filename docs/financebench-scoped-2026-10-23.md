# FinanceBench check — 2026-10-23

This note reports two different retrieval layers. They are not interchangeable.

- **Evidence-page benchmark:** FinanceBench supplies the evidence page; the retriever ranks only those evidence pages.
- **Full-filing benchmark:** the retriever ranks pages inside the downloaded 10-K/10-Q/8-K/earnings PDF.

Both are retrieval measurements. Neither is an answer-accuracy or human-relevance study.

## Evidence-page benchmark

- Data: `PatronusAI/financebench`, 150 public rows, CC-BY-NC-4.0.
- Scope: `company_year`, matching the product's selected-company/as-of flow.
- Retrieval: coverage rerank, no model, no network.
- Chunking: `section`, 3,000 characters, 120 overlap.
- Parent window: 4,000 characters.
- Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_financebench.py `
  --mode coverage --scope company_year --chunk-strategy section --chunk-size 3000 `
  --parent-chars 4000 `
  --home .financebench-eval-cy `
  --output artifacts\financebench-eval-section3000-p4000.json
```

| Metric | Result |
|---|---:|
| Questions | 150 |
| Page hit@1 | 0.667 |
| Page hit@5 | **0.920** |
| Snippet hit@1 | 0.600 |
| Snippet hit@3 | 0.860 |
| Snippet hit@5 | **0.907** |
| MRR@5 | **0.779** |
| Answer-supported top1 | 0.413 |
| Answer-supported at 5 | 0.467 |
| Retrieval P95 | 6.8 ms |

The 4,000-character parent window is the balanced setting. A 6,000-character window reaches snippet hit@5 0.913, but adds context without improving answer-supported top1; it is not the default. Chunk size was not increased further.

### By question type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| domain-relevant | 50 | 0.820 | 0.820 |
| metrics-generated | 50 | 1.000 | 0.980 |
| novel-generated | 50 | 0.940 | 0.920 |

### By document type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| 10-K | 112 | 0.929 | 0.920 |
| 10-Q | 15 | 0.933 | 0.867 |
| 8-K | 9 | 1.000 | 1.000 |
| Earnings | 14 | 0.786 | 0.786 |

Domain-relevant and earnings questions remain the weak groups. Table-aware chunking at the same size did not improve them enough to justify the snippet loss.

### Answer support

`answer_supported_top1` is 0.413 while `answer_supported_at_5` is 0.467. The 5.3-point gap is a ranking problem, not a parent-window problem: page-level answer support is also 0.413. Seven of the eight headroom questions have their supporting page at rank 2.

## Full-filing benchmark

- 80 documents selected; 61 parsed PDFs; 113 questions.
- Files returning HTTP 403 or corrupt PDFs are skipped.
- Retrieval: statement/topic router plus BM25, compact title matching and page priors; no embedding model as the main path.
- Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_financebench_pdf.py `
  --docs 80 --questions 20 --retrieval router --strategy section `
  --output artifacts\financebench-pdf-router80docs.json
```

| Metric | Result |
|---|---:|
| Questions | 113 |
| Parsed documents | 61 |
| Page hit@1 | 0.327 |
| Page hit@5 | **0.850** |
| Snippet hit@5 | **0.779** |
| MRR@5 | 0.523 |

The 22 questions from the 60→80 document extension reached page hit@5 **0.773**. This is the closest thing to a held-out check; it is still small and document-selected.

## What changed

- Topic routing now uses title pages plus body topic phrases, so continuation pages without headings can still be retrieved.
- Added routes for tax, legal, segment, earnings, acquisitions, geographic, cash-conversion and capital-metric questions.
- DPO and cash-conversion questions can route to more than one statement.
- TOC/index pages are down-weighted.
- Segment-result pages with a `managed basis` table get a page-level bonus.

## Limitations

- Evidence-page retrieval is not full 10-K search. The 0.920 number must not be presented as full-filing accuracy.
- The full-filing result is 0.850 page hit@5 on 61 parsed documents; page@1 0.327 and MRR 0.523 are still weak.
- 19 of 80 selected documents were skipped due to HTTP 403, corrupt files, or no extractable text. That is selection bias.
- The router contains domain and bank-report heuristics. It is development-tuned on these rows; the 22-question held-out extension is too small for a final claim.
- Snippet matching uses normalized token overlap; answer support checks numeric or answer-substring presence. Neither is human relevance.
- No model answer was generated. This is retrieval, not end-to-end answer accuracy.
- FinanceBench is CC-BY-NC-4.0; the dataset and PDFs are gitignored and not redistributed.

## Next

1. Add a real held-out split with more than 22 questions.
2. Rerank top1 for answer support rather than growing chunks.
3. Improve domain-relevant and earnings-release pages with table-aware retrieval, not larger fixed chunks.
4. Keep full-filing and evidence-page claims separate in every public document.
