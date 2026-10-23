# External evaluation datasets

The project's internal 200-question gold set is useful for regression testing but
it is **corpus-anchored and author-reviewed**. External datasets give a second,
independent view. They do not replace the internal gold, and they are not
automatically better: each has a different task, license, and label quality.

## Shortlist

| Dataset | Hugging Face ID | License | Size | Fields | Best for | Main caveat |
|---|---|---|---|---|---|---|
| **FinanceBench** | `PatronusAI/financebench` | CC-BY-NC-4.0 | 150 public rows (~0.4 MB parquet) | `question`, `answer`, `evidence_text`, `evidence_page_num`, `doc_name`, `company`, `doc_type`, `doc_period`, `doc_link` | SEC 10-K/10-Q question answering with evidence; closest to FilingCopilot | Non-commercial license; 150-row open subset; evidence page, not a full filing corpus |
| **DocFinQA** | `kensho/DocFinQA` | MIT | 7,437 questions; 4.7 GB original; test split 922 rows / 56 MB parquet | `Context`, `Question`, `Program`, `Answer` | Long-context 10-K/10-Q QA; stress-testing context selection | No evidence span; full 10-K context is huge; not a retrieval benchmark out of the box |
| **FiQA** | `BeIR/fiqa` + `BeIR/fiqa-qrels` | CC-BY-SA-4.0 | 57,638 passages; 6,648 queries; 17,110 qrels | `_id`, `title`, `text`; `query-id`, `corpus-id`, `score` | Standard financial passage retrieval; nDCG@10, Recall@100, MRR | General financial Q&A, not SEC filings; semantic task, lexical baselines are weak |
| **FinMTEB** | GitHub `yixuantt/FinMTEB` | Check repository | Multiple tasks | Embedding benchmark tasks | Comparing embedding models on finance retrieval | More setup; not a single ready-to-use QA file |

## What I would use, in order

### 1. FinanceBench — the closest external product benchmark

Use it for **answer + evidence** evaluation, not exact-passage retrieval:

- Question: `question`
- Gold answer: `answer`
- Gold evidence: `evidence[*].evidence_text` and `evidence_page_num`
- Filing metadata: `doc_name`, `company`, `doc_type`, `doc_period`, `doc_link`

A practical evaluation is:

1. Retrieve the top-k passages from the filing or from the evidence corpus.
2. Ask the model to answer with citations.
3. Check whether the answer is supported by the gold `evidence_text` and whether
   the cited passage overlaps the gold page/text.
4. Report answer correctness, citation support, and abstention separately.

It is **not** a drop-in replacement for the internal exact-passage gold because
the public rows expose evidence pages, not every page of every filing.

Fetch a local copy:

```powershell
.\.venv\Scripts\python.exe scripts\fetch_financebench.py
```

The output is `evaluation/external/data/financebench.jsonl` plus a manifest with
the source URL, license, row count, and SHA256. The `data/` directory is
gitignored; the dataset is not committed.

**Measured result (2026-10-23):** evidence-page benchmark: 150 public rows, 163 evidence pages, company+year scope, section-aware 3,000-character chunks with a 4,000-character parent window: **page hit@5 0.920 / snippet hit@5 0.907 / answer-supported top1 0.413 / MRR 0.779**. Full-filing benchmark: 61 parsed PDFs and 113 questions: **page hit@5 0.850 / snippet hit@5 0.779 / MRR 0.523**; the 22-question new-document check is 0.773 page hit@5. The earlier global-corpus number (0.507/0.407) is retired because it ignored the product's selected-company scope. Full report:
[`docs/financebench-scoped-2026-10-23.md`](../../docs/financebench-scoped-2026-10-23.md).

### 2. FiQA — the standard retrieval benchmark

Use it when you want a number that other retrieval systems also report:

- Corpus: `BeIR/fiqa` config `corpus`
- Queries: `BeIR/fiqa` config `queries`
- Qrels: `BeIR/fiqa-qrels`, test split

Report **nDCG@10**, **Recall@100**, and **MRR@10**. Do not compare a FiQA score
with the internal 0.430 exact-passage score; the tasks and labels are different.

A minimal workflow with the optional Hugging Face stack:

```powershell
python -m pip install datasets ir_measures
python -c "from datasets import load_dataset; c=load_dataset('BeIR/fiqa','corpus'); q=load_dataset('BeIR/fiqa','queries'); r=load_dataset('BeIR/fiqa-qrels'); print(c, q, r)"
```

### 3. DocFinQA — long-context SEC QA

Use the **test split** (922 rows, ~56 MB parquet) for a first pass. The full
dataset is 4.7 GB because each row carries a long 10-K/10-Q context.

```powershell
python -m pip install datasets
python -c "from datasets import load_dataset; d=load_dataset('kensho/DocFinQA', split='test'); print(d[0].keys())"
```

DocFinQA has no evidence span. You can still evaluate:

- answer accuracy (numeric/string match);
- whether the answer string appears in the retrieved context;
- context-token cost and latency.

It is better for a **long-context QA** claim than for a **retrieval** claim.

## Credit-risk documents

There is no public, labeled dataset of bank credit policies or SR 11-7 model
validation reports. The internal `examples/credit-risk` corpus is synthetic for
that reason. For a credible credit-risk demo:

1. Use a **desensitized real policy/validation document** from a willing
   colleague, with written permission.
2. Have 2–3 risk reviewers label 30–50 question/answer/evidence triples.
3. Keep the synthetic corpus for public demos and the real corpus private.

Public regulatory documents (SR 11-7, Federal Reserve supervisory letters, SEC
comment letters) can provide a realistic corpus, but they do not provide labels.
They are useful for retrieval demos, not for accuracy claims.

## Licensing notes

- **FinanceBench:** CC-BY-NC-4.0. Fine for a non-commercial portfolio and
  research; do not use it in a commercial product without checking the license.
- **DocFinQA:** MIT. Permissive; keep the attribution.
- **FiQA:** CC-BY-SA-4.0. Share-alike; keep the attribution and license notice.
- **FinMTEB:** check the repository's license before redistribution.

Do not commit downloaded datasets to the repository unless the license and size
clearly allow it. The fetcher writes to a gitignored `data/` directory.
