# FinanceBench check — 2026-10-23

This note reports two different retrieval layers. They are not interchangeable.

- **Headline:** full-filing retrieval is **page hit@5 0.850, page@1 0.478, snippet hit@5 0.858, MRR 0.624** on 61 parsed PDFs and 113 questions; the 22-question new-document check is 0.773.
- **Diagnostic:** evidence-page retrieval is **page hit@5 0.920, page@1 0.707, snippet hit@5 0.920, answer-supported top1 0.913 on the 69 evaluable rows (raw 0.427), MRR 0.798** on 150 rows. It ranks known evidence pages; it is not full 10-K search.

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
| Page hit@1 | 0.707 |
| Page hit@5 | **0.920** |
| Snippet hit@1 | 0.653 |
| Snippet hit@3 | 0.873 |
| Snippet hit@5 | **0.920** |
| MRR@5 | **0.798** |
| Answer-supported top1, raw | 0.427 |
| Answer-supported top1, page-level | 0.447 |
| Answer-supported at 5, raw | 0.467 |
| Answer-evaluable questions | 69 |
| Answer-supported top1, evaluable | 0.913 |
| Answer-supported at 5, evaluable | 1.000 |
| Answer-supported top1, page-level evaluable | 0.957 |
| Retrieval P95 | 6.5 ms |

The 4,000-character parent window is the balanced setting. Larger windows were not adopted because they did not improve answer-supported top1. Chunk size was not increased further.

### By question type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| domain-relevant | 50 | 0.820 | 0.820 |
| metrics-generated | 50 | 1.000 | 1.000 |
| novel-generated | 50 | 0.940 | 0.940 |

### By document type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| 10-K | 112 | 0.929 | 0.929 |
| 10-Q | 15 | 0.933 | 0.933 |
| 8-K | 9 | 1.000 | 1.000 |
| Earnings | 14 | 0.786 | 0.786 |

Domain-relevant and earnings questions remain the weak groups. Table-aware chunking at the same size did not improve them enough to justify the snippet loss.

### Answer support

Only **69/150** questions have the answer literally present in the gold evidence page; 49/50 `metrics-generated` answers are computed and not written on the page. On those 69 evaluable rows, answer-supported top1 is **0.913**, at 5 is **1.000**, and page-level top1 is **0.957**. The raw 0.427/0.467 numbers are retained for transparency, but they mix in calculation answers that no evidence page contains and should not be used as a retrieval headline.

## Full-filing benchmark

- 80 documents selected; 61 parsed PDFs; 113 questions.
- Files returning HTTP 403 or corrupt PDFs are skipped.
- Retrieval: statement/topic router plus BM25, compact title matching and page priors; no embedding model as the main path.
- Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_financebench_pdf.py `
  --docs 80 --questions 20 --retrieval router --strategy section `
  --top1-rerank title --rerank flashrank `
  --output artifacts\financebench-pdf-router80docs.json
```

| Metric | Result |
|---|---:|
| Questions | 113 |
| Parsed documents | 61 |
| Page hit@1 | 0.478 |
| Page hit@3 | 0.779 |
| Page hit@5 | **0.850** |
| Snippet hit@1 | 0.487 |
| Snippet hit@3 | 0.779 |
| Snippet hit@5 | **0.858** |
| MRR@5 | 0.624 |

The 22 questions from the 60→80 document extension reached page hit@5 **0.773**. This is the closest thing to a held-out check; it is still small and document-selected.

## What changed

- Topic routing now uses title pages plus body topic phrases, so continuation pages without headings can still be retrieved.
- Added routes for tax, legal, segment, earnings, acquisitions, geographic, cash-conversion and capital-metric questions.
- DPO and cash-conversion questions can route to more than one statement.
- A title-based top1 rerank promotes a top-3 page whose heading matches more query terms. In full-filing it raised page@1 0.327→0.407 and MRR 0.523→0.569 without changing page@5.
- A FlashRank cross-encoder rerank on the top5 full-filing pages then raised page@1 0.407→0.478, MRR 0.569→0.624 and snippet@1 0.416→0.487; page@5 and snippet@5 stayed unchanged. It adds roughly 0.3s per query, so evidence-page keeps the cheaper title rerank.
- Snippet matching now also uses a compact character match, so PDF spacing/table-extraction differences such as `Cashandcashequivalents` no longer count as misses. Full-filing snippet@5 rose 0.779→0.858; evidence-page snippet@5 rose 0.907→0.920.
- TOC/index pages are down-weighted.
- Segment-result pages with a `managed basis` table get a page-level bonus.

## Additional full-filing ablations (2026-10-24)

The retained baseline is page@1 0.478, page@5 0.850, snippet@5 0.858,
MRR@5 0.624. Four candidate changes were measured and rejected:

| Change | Page@1 | Page@5 | Snippet@5 | MRR@5 | Verdict |
|---|---:|---:|---:|---:|---|
| Top20 candidates + FlashRank, then top5 | 0.398 | 0.805 | 0.814 | 0.546 | Reject: reranker promotes distractors |
| Union router filter with lexical fallback pages | 0.310 | 0.540 | 0.549 | 0.397 | Reject: title/topic filter is load-bearing |
| Query-focused FlashRank window instead of page head | 0.442 | 0.850 | 0.858 | 0.606 | Reject: page head/title context matters |
| Insert adjacent pages after top hits | 0.478 | 0.690 | 0.690 | 0.537 | Reject: neighbors displace exact gold pages |

These are negative results for this 113-question set, not evidence that the
methods cannot work with a stronger page index or layout-aware extraction.

## Limitations

- Evidence-page retrieval is not full 10-K search. The 0.920 number must not be presented as full-filing accuracy.
- The full-filing result is 0.850 page hit@5 on 61 parsed documents; page@1 0.478 and MRR 0.624 are improved but still not strong.
- 19 of 80 selected documents were skipped due to HTTP 403, corrupt files, or no extractable text. That is selection bias.
- The router contains domain and bank-report heuristics. It is development-tuned on these rows; the 22-question held-out extension is too small for a final claim.
- Title and FlashRank rerankers are development-tuned on the 150 evidence-page rows and 113 full-filing questions; their gain needs a held-out confirmation. FlashRank adds roughly 0.3s per query.
- Snippet matching uses normalized token and compact-character matching; answer support checks numeric or answer-substring presence. Neither is human relevance.
- No model answer was generated. This is retrieval, not end-to-end answer accuracy.
- FinanceBench is CC-BY-NC-4.0; the dataset and PDFs are gitignored and not redistributed.

## Next

1. Add a real held-out split with more than 22 questions.
2. Validate the title rerank on that split; do not tune it further on the same 150 rows.
3. Improve domain-relevant and earnings-release pages with table-aware retrieval, not larger fixed chunks.
4. Keep full-filing and evidence-page claims separate in every public document.
