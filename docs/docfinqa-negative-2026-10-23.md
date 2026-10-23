# DocFinQA is not a chunking benchmark

**Date:** 2026-10-23  
**Status:** negative dataset-selection result; do not use these numbers to rank chunking strategies.

## What was tested

- `kensho/DocFinQA` test split, first 10 rows, MIT license.
- Context length: 249,377–1,595,159 characters (average 620,554).
- Chunking strategies: fixed, recursive, section, section_table, table, sentence, child.
- Proxy: does the gold `Answer` string appear in the top-k chunk or its 2,500-character parent window?

## The problem

DocFinQA answers are often **computed**, not copied from the context. The `Program` field contains the arithmetic:

```text
net_revenue_change = 5829 - 5735
answer = 94
```

Only 4 of the 10 answers appear anywhere in the context; 6 do not:

| # | Answer | In context? |
|---|---|---:|
| 1 | 94 | yes |
| 2 | 14% | no |
| 3 | 9.9% | no |
| 4 | 2.9% | no |
| 5 | 7% | yes |
| 6 | 10% | yes |
| 7 | 12% | yes |
| 8 | -35 | no |
| 9 | -4.4% | no |
| 10 | 24.6% | no |

The resulting `chunk@5` scores were 0.00–0.10 depending on strategy. That is not evidence that section chunking is bad; it is evidence that **answer-string containment is the wrong proxy for a computed-answer dataset**.

DocFinQA also has no evidence span, so there is no page/passage to retrieve. It is useful for long-context QA with program execution, not for retrieval or chunking evaluation.

## What to use instead

- **FinanceBench**: has `evidence_text`, evidence page, question, answer, and filing metadata. It is the right public dataset for evidence-page retrieval and chunking comparisons.
- **Full filing PDFs** from FinanceBench `doc_link`: the next step for real full-document retrieval with evidence pages.
- **FiQA**: has corpus/queries/qrels for standard retrieval (nDCG@10), but passages are short and already paragraph-sized, so it does not test chunking. Use it as a retrieval-quality check, not a chunking benchmark.
- **Internal SEC corpus**: has exact gold passage IDs; reconstructing full filing text from the source cache would give a second in-domain chunking benchmark.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts\fetch_docfinqa.py --limit 10
.\.venv\Scripts\python.exe scripts\evaluate_docfinqa.py --limit 10
```

The output is `artifacts/docfinqa-chunking.json`. Treat it as a dataset-selection negative result, not a ranking of chunking strategies.
