# Reproducing the evaluation

This is a **local reproduction guide**, not a clean-room certification. The
datasets, PDFs and model weights are gitignored; network and upstream file
availability can change the parsed-document set.

## Prerequisites

- Python 3.10–3.12; the recorded runs used Python 3.11.
- `pip install -e ".[evaluation]"`
- Network access for SEC/EDGAR, Hugging Face, and market providers.
- The exact commit used for a run. Record it with `git rev-parse HEAD`.

## Data and models

```powershell
# FinanceBench JSONL + manifest; PDFs are cached under evaluation/external/data/
.\.venv\Scripts\python.exe scripts\fetch_financebench.py

# BGE INT8 ONNX reranker (570,727,094 bytes)
.\.venv\Scripts\python.exe scripts\fetch_bge_reranker.py

# FinDER parquet is fetched by the evaluator on first use
.\.venv\Scripts\python.exe scripts\evaluate_finder.py --scope company --rerank flashrank
```

If `huggingface.co` is unavailable, pass a mirror:

```powershell
.\.venv\Scripts\python.exe scripts\fetch_bge_reranker.py `
  --base-url https://hf-mirror.com/onnx-community/bge-reranker-v2-m3-ONNX/resolve/main
```

The fetcher verifies the ONNX file's size and SHA256:
`912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d`.

## Commands

```powershell
# Full-filing: 80 selected docs, 61 parsed, 113 questions
.\.venv\Scripts\python.exe scripts\evaluate_financebench_pdf.py `
  --docs 80 --questions 20 --retrieval router --strategy section `
  --top1-rerank title --rerank bge `
  --output artifacts\financebench-pdf-router80docs.json

# Evidence-page: 150 rows, company+year, section 3000 / parent 4000
.\.venv\Scripts\python.exe scripts\evaluate_financebench.py `
  --scope company_year --chunk-strategy section --chunk-size 3000 `
  --parent-chars 4000 --top1-rerank title --rerank off `
  --output artifacts\financebench-eval-section3000-p4000.json

# FinDER: 1,085 reasoning rows, company scope with category fallback
.\.venv\Scripts\python.exe scripts\evaluate_finder.py `
  --scope company --rerank flashrank --output artifacts\finder-eval.json

# The evidence-page evaluator refuses to delete a nonempty unmanaged --home.
# Use a fresh directory or pass --force deliberately after checking the path.

# Full test suite
.\.venv\Scripts\python.exe -m pytest -q
```

## Expected results

These are the latest recorded values, with 95% Wilson intervals where the
sample is binomial. They are not guaranteed to reproduce byte-for-byte if
upstream PDFs change or return 403. Answer support reports full/partial/
unsupported: full requires every key number, unit and period in the evidence
set; verified calculation inputs can support a result that is not copied
verbatim. The earlier any-one-number values are retired and are not comparable.

| Check | Estimate | n | 95% interval |
|---|---:|---:|---:|
| Full-filing page@1 | 0.540 | 113 | 0.448–0.629 |
| Full-filing page@5 | 0.850 | 113 | 0.772–0.904 |
| Full-filing snippet@5 | 0.858 | 113 | 0.782–0.911 |
| 22-question extension page@5 | 0.773 | 22 | 0.566–0.899 |
| Evidence-page page@5 | 0.920 | 150 | 0.866–0.954 |
| Evidence-page full answer top1 | 0.207 | 150 | 0.150–0.279 |
| Evidence-page full answer at5 | 0.287 | 150 | 0.221–0.364 |
| Evidence-page full answer top1, full-evaluable | 0.738 | 42 | 0.589–0.847 |
| FinDER full answer top1 | 0.098 | 1,085 | 0.081–0.117 |
| FinDER full answer at5 | 0.160 | 1,085 | 0.140–0.183 |
| FinDER full answer top1, full-evaluable | 0.409 | 259 | 0.351–0.470 |

Support records: [FinDER](validation/finder-support-2026-10-26.json) and
[FinanceBench](validation/financebench-support-2026-10-26.json).

## Known reproducibility limits

- 19 of 80 selected filing documents were skipped because of HTTP 403, corrupt
  PDFs, or no extractable text. A different network day can change the set.
- Full-filing and held-out results are development-tuned on these questions;
  they are not an untouched final test.
- BGE adds about 1.84s P95 on CPU; evidence-page keeps title rerank.
- The live 140-question model run is **not** reproduced by these commands; it
  needs provider credentials and the latest retained record reports P95 174.9s /
  3.75M tokens.
- Self-review and independent review status are described in
  [evaluation.md](evaluation.md). Do not label these numbers as human accuracy.
