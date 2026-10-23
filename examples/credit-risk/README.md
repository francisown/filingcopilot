# Credit-risk vertical demo

A self-contained demonstration that the FilingCopilot evidence layer is not tied to SEC filings. The corpus is swapped for synthetic retail-credit documents:

- `corpus/credit-policy-v3.md` — DTI/LTV limits, exception approvals, income verification, revalidation triggers.
- `corpus/model-validation-report-2026.md` — SR 11-7-style independent validation findings, limitations, required actions.
- `corpus/portfolio-performance-2026Q2.md` — segment delinquency and coverage reporting.

The demo reuses the same building blocks as the main project: SQLite FTS5 corpus, heading-scoped passages, citation locators, a frozen evidence package with a SHA256, and an offline verifier. It does **not** call a language model. The answer is an extractive summary of retrieved passages, which keeps the demo deterministic and free to run.

## Why this exists

A generic RAG demo shows that retrieval works. A credit-risk reviewer asks different questions:

- Can every number be traced to a policy clause or a validation finding?
- Can the evidence package be re-checked after the model, the index, or the machine changes?
- Does the system keep the source document hash and the exact quote?
- Can a reviewer see what the system did **not** find?

This demo answers those questions on a small, inspectable corpus. It is a portfolio demonstration, not a production credit-decision system.

## Quick start

From the repository root, with the project environment installed:

```powershell
# 1. Ingest the synthetic corpus into examples/credit-risk/.data
python examples/credit-risk/demo.py ingest --reset

# 2. Ask a question; prints cited passages and writes a frozen package
python examples/credit-risk/demo.py ask "What is the maximum DTI for a prime unsecured loan?"

# 3. Verify the package offline: package hash, document hash, quote span
python examples/credit-risk/demo.py verify examples/credit-risk/packages/<package>.json

# 4. Run the frozen 20-question set
python examples/credit-risk/demo.py eval

# 5. Run the whole path in one command
python examples/credit-risk/demo.py demo
```

`demo` resets `.data`, ingests the corpus, asks three questions, builds and verifies one package, then runs the evaluation. No API key, network, GPU, or model is required.

## What the verifier checks

`verify_package` recomputes the package hash and, for every citation:

1. The source document exists.
2. The document SHA256 matches the hash recorded at ingestion.
3. The quoted text is a verbatim, contiguous span in the source document (whitespace-normalized).
4. The package hash still matches the canonical JSON.

If the model or the index changes, the package hash still identifies what was reviewed. If a source document changes, the document hash fails. If a quote is edited, the span check fails. The verifier does not decide whether an answer is semantically correct; it checks that the evidence behind the answer is intact.

## Evaluation

The frozen set contains 20 bilingual-style credit-risk questions with an expected document, section, and key terms. The current implementation uses two-stage retrieval:

1. SQLite FTS5 generates candidates.
2. A deterministic reranker scores IDF-weighted term coverage plus 2–3 word phrase matches.

Current result on the 20-question set:

| Metric | Result |
|---|---:|
| Document hit@3 | 100% |
| Section hit@3 | 80% |
| Term hit@3 | 95% |
| MRR | 0.975 |

These numbers measure retrieval and section localization on synthetic documents. They do **not** measure answer correctness, and the question set is small and authored by the same person who wrote the corpus. The section misses are reported rather than hidden; q08, q09, q11, and q16 retrieve the right validation document but not always the exact subsection.

## Mapping to credit-risk work

| FilingCopilot concept | Credit-risk equivalent |
|---|---|
| SEC filing | Credit policy, model validation report, portfolio pack |
| Filing passage | Policy clause, finding, limitation, segment table |
| Financial fact | DTI limit, LTV cap, PD, DPD, coverage ratio |
| Evidence ledger | Clause-level citation with document hash and line locator |
| Numeric gate | Recompute a limit or ratio from the cited source |
| Frozen package | Validation workpaper with reproducible evidence |
| Offline verifier | Independent re-performance of the evidence checks |
| Model-risk review | SR 11-7-style conceptual soundness, lineage, limitations, human review |

## Limitations

- The documents are synthetic. Do not use them for lending, validation, or regulatory decisions.
- The answer is extractive. A model can be added to synthesize, but the same verifier must then check the generated claims, not just the retrieved quotes.
- The demo does not implement a credit decision engine, a scorecard, or a regulatory reporting workflow.
- Retrieval is lexical plus deterministic reranking. Production credit corpora would need access control, PII handling, document versioning, and a much larger evaluation set.
- The evaluation is self-authored and not independently reviewed.

## Files

```
examples/credit-risk/
  demo.py                 ingest, ask, package, verify, eval
  questions.json          20 frozen questions with expected evidence
  corpus/                 synthetic policy, validation, and portfolio documents
  packages/               generated evidence packages (gitignored)
  .data/                  generated SQLite corpus (gitignored)
```

The main project’s architecture is described in [`docs/architecture.md`](../../docs/architecture.md). The evaluation method for the SEC corpus is in [`docs/evaluation.md`](../../docs/evaluation.md).
