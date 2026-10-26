# FinanceBench check — 2026-10-23, support update 2026-10-26

This note reports two different retrieval layers. They are not interchangeable.

- **Headline:** full-filing retrieval is **page hit@5 0.850, page@1 0.478, snippet hit@5 0.858, MRR 0.624** on 61 parsed PDFs and 113 questions; the 22-question new-document check is 0.773.
- **Diagnostic:** evidence-page retrieval is **page hit@5 0.920, page@1 0.687, snippet hit@5 0.920, answer full/partial/unsupported top1 0.207/0.133/0.660 raw; page-level 0.213/0.147/0.640; 42 full-evaluable rows 0.738 top1 / 0.929 at5; MRR 0.784** on 150 rows. It ranks known evidence pages; it is not full 10-K search. The earlier 0.427/0.913 any-one-number values are retired.

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

The evaluator refuses to delete a nonempty unmanaged `--home`. Use a fresh
directory, or pass `--force` deliberately after confirming the path. The
repository root, current directory and user home are always rejected.

| Metric | Result |
|---|---:|
| Questions | 150 |
| Page hit@1 | 0.687 |
| Page hit@5 | **0.920** |
| Snippet hit@1 | 0.653 |
| Snippet hit@3 | 0.873 |
| Snippet hit@5 | **0.920** |
| MRR@5 | **0.784** |
| Full answer top1, raw | 0.213 |
| Partial answer top1, raw | 0.140 |
| Unsupported answer top1, raw | 0.647 |
| Full answer top1, page-level | 0.227 |
| Full answer at 5, raw | 0.293 |
| Partial answer at 5, raw | 0.173 |
| Unsupported answer at 5, raw | 0.533 |
| Full-evaluable questions | 42 |
| Full answer top1, full-evaluable | 0.738 |
| Full answer at 5, full-evaluable | 0.929 |
| Full answer top1, page-level full-evaluable | 0.762 |
| Retrieval P95 | 6.8 ms |

The 4,000-character parent window is the balanced setting. Larger windows were not adopted because they did not improve answer-supported top1. Chunk size was not increased further.

### By question type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| domain-relevant | 50 | 0.860 | 0.860 |
| metrics-generated | 50 | 1.000 | 1.000 |
| novel-generated | 50 | 0.900 | 0.900 |

### By document type

| Type | Questions | Page hit@5 | Snippet hit@5 |
|---|---:|---:|---:|
| 10-K | 112 | 0.938 | 0.938 |
| 10-Q | 15 | 0.933 | 0.933 |
| 8-K | 9 | 1.000 | 1.000 |
| Earnings | 14 | 0.714 | 0.714 |

Domain-relevant and earnings questions remain the weak groups. Table-aware chunking at the same size did not improve them enough to justify the snippet loss.

### Answer support

Only **42/150** questions have a full answer in the gold evidence set; 28 are partial and 80 unsupported. The classifier requires all key numbers, units and periods, and can accept verified calculation inputs instead of a literal result. On the 42 full-evaluable rows, full top1 is **0.738**, at 5 is **0.929**, and page-level top1 is **0.762**. Partial is not counted as full. The raw 0.207/0.287 numbers mix in calculation answers that no evidence page contains and should not be used as a retrieval headline.

BGE was also measured on the evidence-page path: page@1 0.707→**0.720**,
page@3 0.873→**0.913**, MRR 0.798→**0.808**; the earlier page@5 was **0.920**.
The 2026-10-26 support rerun with the bounded coverage candidate pool records
page@5 **0.920**, but it still uses title rerank. P95 rises to **1.46s** with
BGE, so the fast evidence-page path keeps title rerank; BGE is enabled only for
the full-filing path.

## Full-filing benchmark

- 80 documents selected; 61 parsed PDFs; 113 questions.
- Files returning HTTP 403 or corrupt PDFs are skipped.
- Retrieval: statement/topic router plus BM25, compact title matching and page priors; no embedding model as the main path.
- Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_financebench_pdf.py `
  --docs 80 --questions 20 --retrieval router --strategy section `
  --top1-rerank title --rerank bge `
  --output artifacts\financebench-pdf-router80docs.json
```

| Metric | Result |
|---|---:|
| Questions | 113 |
| Parsed documents | 61 |
| Page hit@1 | **0.540** |
| Page hit@3 | 0.788 |
| Page hit@5 | **0.850** |
| Snippet hit@1 | **0.531** |
| Snippet hit@3 | 0.796 |
| Snippet hit@5 | **0.858** |
| MRR@5 | **0.664** |
| Rerank P95 | 1.84 s |

The 22 questions from the 60→80 document extension reach page hit@1 **0.545**, page hit@5 **0.773** and MRR **0.621** with BGE. This is the closest thing to a held-out check; it is still small and document-selected.

## What changed

- Topic routing now uses title pages plus body topic phrases, so continuation pages without headings can still be retrieved.
- Added routes for tax, legal, segment, earnings, acquisitions, geographic, cash-conversion and capital-metric questions.
- DPO and cash-conversion questions can route to more than one statement.
- A title-based top1 rerank promotes a top-3 page whose heading matches more query terms. In full-filing it raised page@1 0.327→0.407 and MRR 0.523→0.569 without changing page@5.
- A FlashRank cross-encoder rerank on the top5 full-filing pages then raised page@1 0.407→0.478, MRR 0.569→0.624 and snippet@1 0.416→0.487; page@5 and snippet@5 stayed unchanged. It adds roughly 0.3s per query, so evidence-page keeps the cheaper title rerank.
- A `BAAI/bge-reranker-v2-m3` INT8 ONNX rerank on the same top5 pages raised page@1 0.478→0.540, snippet@1 0.487→0.531 and MRR 0.624→0.664, while page@5 0.850 and snippet@5 0.858 stayed unchanged. It costs about 1.8s P95 on CPU; full-filing adopts it, FlashRank remains the fallback.
- Snippet matching now also uses a compact character match, so PDF spacing/table-extraction differences such as `Cashandcashequivalents` no longer count as misses. Full-filing snippet@5 rose 0.779→0.858; evidence-page snippet@5 rose 0.907→0.920 in the support rerun.
- TOC/index pages are down-weighted.
- Segment-result pages with a `managed basis` table get a page-level bonus.

## Additional full-filing ablations (2026-10-24)

The earlier FlashRank baseline was page@1 0.478, page@5 0.850, snippet@5
0.858, MRR@5 0.624; the retained BGE top5 is page@1 0.540 / page@5 0.850 /
snippet@5 0.858 / MRR 0.664. Candidate changes measured against the FlashRank
baseline were rejected:

| Change | Page@1 | Page@5 | Snippet@5 | MRR@5 | Verdict |
|---|---:|---:|---:|---:|---|
| Top20 candidates + FlashRank, then top5 | 0.398 | 0.805 | 0.814 | 0.546 | Reject: reranker promotes distractors |
| Union router filter with lexical fallback pages | 0.310 | 0.540 | 0.549 | 0.397 | Reject: title/topic filter is load-bearing |
| Query-focused FlashRank window instead of page head | 0.442 | 0.850 | 0.858 | 0.606 | Reject: page head/title context matters |
| Insert adjacent pages after top hits | 0.478 | 0.690 | 0.690 | 0.537 | Reject: neighbors displace exact gold pages |
| BGE top20 candidates, then top5 | 0.487 | 0.832 | 0.850 | 0.624 | Reject: page@5 drops |

These are negative results for this 113-question set, not evidence that the
methods cannot work with a stronger page index or layout-aware extraction.

## Limitations

- Evidence-page retrieval is not full 10-K search. The 0.920 number must not be presented as full-filing accuracy.
- The full-filing result is 0.850 page hit@5 on 61 parsed documents; BGE rerank improves page@1 to 0.540 and MRR to 0.664, but these are still not strong.
- 19 of 80 selected documents were skipped due to HTTP 403, corrupt files, or no extractable text. That is selection bias.
- The router contains domain and bank-report heuristics. It is development-tuned on these rows; the 22-question held-out extension is too small for a final claim.
- Title, FlashRank and BGE rerankers are development-tuned on the 150 evidence-page rows and 113 full-filing questions; their gain needs a held-out confirmation. FlashRank adds roughly 0.3s per query; BGE INT8 ONNX adds roughly 1.8s P95 on CPU. The 22-question BGE extension reaches page@1 0.545 / page@5 0.773 / MRR 0.621, still too small for a final claim.
- Snippet matching uses normalized token and compact-character matching. Answer support reports full/partial/unsupported: full requires every key number, unit and period, and allows verified calculation inputs to support a result not copied verbatim; numeric substrings do not count. Neither metric is human relevance.
- No model answer was generated. This is retrieval, not end-to-end answer accuracy.
- FinanceBench is CC-BY-NC-4.0; the dataset and PDFs are gitignored and not redistributed.

## Next

1. Add a real held-out split with more than 22 questions.
2. Validate the title rerank on that split; do not tune it further on the same 150 rows.
3. Improve domain-relevant and earnings-release pages with table-aware retrieval, not larger fixed chunks.
4. Keep full-filing and evidence-page claims separate in every public document.
