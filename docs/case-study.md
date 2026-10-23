# Case study: an evidence-grounded research agent for company filings

**Summary.** FilingCopilot is a local-first research workspace for company filings. It answers questions about SEC disclosures, calculates financial measures, and returns the evidence behind every answer. The strongest verified result is not a benchmark score; it is that **116 numeric claims matched SEC CompanyFacts or an independent arithmetic check with zero mismatches**, and every saved answer can be exported as a frozen package and re-checked offline. The main limitation is equally clear: the 140-question evaluation is self-reviewed, has no independent human gold standard, and model-using questions had a P95 latency of 417 seconds.

This case study describes what was built, how the evidence layer works, what the evaluation does and does not prove, and what I would do next.

## The problem

Company filings are long, repetitive, and numerically dense. A ten-K can run to hundreds of pages; the same metric appears in several statements, periods, units, and filing versions. A generic chatbot can produce a fluent answer, but an analyst needs more:

- Which filing and which passage support this number?
- Is the number for the fiscal year, the quarter, or the trailing twelve months?
- Is it in the reporting currency or a converted currency?
- Does the calculation use the same period and units as the inputs?
- Can a reviewer re-run the check after the model, the index, or the machine changes?

Those questions are the product. The model is only one component.

## What it does

A user selects a company and an as-of date, then asks a question in English or Chinese. There are two paths:

1. **Data only.** Explicit annual numbers, growth rates, margins, and financial trends are served without an LLM. The answer is deterministic, fast, and available offline.
2. **AI research.** A LangGraph agent plans the work, retrieves filing passages, calls financial tools, drafts an answer, and submits claims for verification. The result includes citations and an explicit list of gaps.

Every answer can be opened at the source passage. A research package can be exported with the question, the claims, the citations, the calculations, the source hashes, and a package hash. The standalone verifier then re-checks the package without the research agent, the model, or the network. If a source document changes, the document hash fails. If a quote is edited, the span check fails. If the package is edited, the package hash fails.

The same service is exposed through a browser workspace, a CLI, a REST API, and **30 MCP tools**. The point of four surfaces is not feature count; it is that the evidence rules are identical regardless of how the question arrives.

## Architecture

The flow is deliberately boring:

```
Web / CLI / REST / MCP
        |
   Research service
        |
  Capability plan and budget
        |
   Tool-using agent (LangGraph)
        |
  Hybrid retrieval: SQLite FTS5 + optional local embeddings
        |
   Evidence ledger: passages, facts, calculations, source hashes
        |
  Deterministic numeric and narrative gates
        |
   Answer + explicit gaps
        |
  Frozen package + SHA256 manifest
        |
   Offline verifier
```

Two design choices matter more than the framework choice.

**First, the evidence ledger is the source of truth, not the chat transcript.** A fact carries company, filing, public date, reporting period, currency, units, source URL, and locator. A calculation carries its inputs and formula. A passage carries its section and line locator. The answer is assembled from those objects, so a citation is a pointer to a stored object rather than a string the model produced.

**Second, verification is layered.** Numeric checks recompute values and compare them with SEC CompanyFacts or an independent arithmetic path. Narrative checks reject explicit currency-direction conflicts and attempts to infer document-wide absence from selected passages. A semantic reviewer then checks subject, direction, period, materiality, and evidence scope. The reviewer is a model judgment; passing it does not certify correctness. That is why the package also exposes the gaps and the review status.

The architecture is described in more detail in [`docs/architecture.md`](architecture.md). The independent verifier is documented in [`docs/independent-verifier.md`](independent-verifier.md).

## Evaluation

The evaluation separates four questions that are often collapsed into one "accuracy" number: Did the workflow deliver an answer? Were the financial calculations correct? Did retrieval find the right passage? Did the narrative claims stay within the evidence?

The recorded run used **140 unchanged bilingual questions**: 100 research questions and 40 boundary questions. The knowledge date was 2026-10-08, the corpus contained 62 filings, 1,166 facts, 39,912 statement facts, and 31,123 passages, and the run used a frozen source snapshot.

| Question group | Complete | Partial | No research answer |
|---|---:|---:|---:|
| Numeric (20) | 20 | 0 | 0 |
| Causes (20) | 14 | 6 | 0 |
| Negation checks (20) | 16 | 4 | 0 |
| Risks (20) | 11 | 8 | 1 |
| Annual comparisons (20) | 20 | 0 | 0 |
| **Research questions (100)** | **81** | **18** | **1** |

The other 40 questions asked about unsupported future-price guarantees or hypothetical market-source failures. All 40 received a specific boundary explanation with no model call. They are recorded as `abstained` by the runner; they are not 40 failed model answers.

The run used models for 60 questions, started 476 HTTP requests, recorded 442 HTTP 200 responses, and reported 4,312,447 tokens. Median latency for model-using questions was 92.79 seconds; P95 was 417.11 seconds. The overall median of 0.075 seconds is dominated by the 80 questions that required no model call and does not describe research latency.

The numeric oracle found **116 exact SEC CompanyFacts or arithmetic matches, 34 numeric claims outside that checker's scope, and zero mismatches**. The 34 unassessed claims are not counted as correct. Saved packages were audited against available raw sources: 100 consistent, none missing sources. The recorded source passed **1,656 tests**; the current branch passes **1,718**, including the new credit-risk demo tests.

Retrieval is measured on two levels. The internal 200-question exact-passage gold is a regression set; it measures strict paragraph identity, not human relevance. The product-scoped external check below is the number to report.

**External retrieval update (2026-10-23).** The earlier FinanceBench global-corpus check was retired: it ignored the product's selected-company scope. The evidence-page benchmark now uses company+year scope, section-aware 3,000-character chunks and a 4,000-character parent window: **page hit@5 0.920, snippet hit@5 0.907, answer-supported top1 0.413, MRR 0.779**. The full-filing benchmark parses 61 PDFs and 113 questions: **page hit@5 0.850, snippet hit@5 0.779, MRR 0.523**; the 22-question new-document check reaches 0.773 page hit@5. These are retrieval measurements, not answer accuracy or human relevance. See [the FinanceBench report](financebench-scoped-2026-10-23.md).

For narrative claims, a first pass over 40 delivered claims recorded 95% supported, 5% unclear, and 0% unsupported. The project author was the sole reviewer and was not a domain specialist. The sample was selected from delivered claims, so it excludes rejected drafts and refusals. Independent domain review remains pending.

### What the evaluation proves

- The workflow can deliver a complete or partial answer for 99 of 100 research questions, with explicit gaps for the rest.
- Deterministic numeric checks can validate a meaningful subset of claims against an external source with zero mismatches in that subset.
- Packages are reproducible enough to be replayed and audited offline.
- Failure modes are visible and have regression coverage.

### What it does not prove

- It does not prove general answer accuracy. There is no independent human gold standard.
- It does not prove that every narrative claim is supported. Model review is not a human reviewer.
- It does not prove production readiness. The latency, cost, and issuer coverage are not production-grade.
- It does not prove human relevance or answer accuracy: the evidence-page check is 0.920 page hit@5 and 0.907 snippet hit@5, while the full-filing check is 0.850 and 0.779 on 61 parsed PDFs. The 22-question new-document check is 0.773 page hit@5.

## Failure taxonomy

The most useful engineering output was not the pass count; it was the failure list. Five examples:

**1. String-encoded integer tool arguments.** A tool received `"5"` where it expected `5`, causing a retrieval `TypeError` in a live run. Fix: normalize unambiguous integer and boolean argument strings before dispatch. Regression: focused tests plus captured search replays.

**2. USD/FX direction reversal.** A semantic review accepted a claim that reversed the direction of a currency effect. The source said weaker foreign currencies and a stronger USD can raise international prices and reduce demand; the draft reversed it. Fix: a narrow pre-review check for explicit currency-direction conflicts. Regression: replay of the captured failing verdict now withholds the claim.

**3. Document-wide absence overreach.** A draft inferred that a filing did not discuss a risk because the selected excerpts did not mention it. Fix: reject document-wide absence claims that are based on selected passages, while keeping directly disclosed negative facts eligible. Regression: the captured negation case now fails before semantic approval.

**4. Numeric-only answers to cause or risk questions.** A draft answered "why did margin change?" with figures and no explanation. Fix: a cause or risk request cannot be completed by numbers alone; valid figures are retained with an explanation gap, and the repair budget can supply prose. Regression: focused completeness tests.

**5. Duplicate facts in the fallback path.** A no-submission fallback repeated the same financial observation three times. Fix: collapse identical observations only in that fallback, after normal verification, while preserving different values, periods, currencies, concepts, and explicit source requests. Regression: one failing case plus nine controls.

A sixth, smaller issue was incomplete English fallback messages for boundary responses. Fix: complete the messages and add localization tests. The point of listing these is not self-flagellation; it is that each failure became a durable check.

## Governance and model-risk angle

The project is not a certified model, and nothing here should be read as regulatory approval. But the evidence layer maps cleanly to the questions a model-risk reviewer asks under SR 11-7-style validation:

| SR 11-7 concern | Project evidence |
|---|---|
| Conceptual soundness | Deterministic numeric gates, documented narrative gates, explicit limitations |
| Data lineage | Filing accession, source URL, public date, period, currency, units, document hash |
| Reproducibility | Frozen inputs, canonical package, SHA256 manifest, standalone verifier |
| Human review | Claim-level review status; independent review explicitly pending |
| Ongoing monitoring | Frozen evaluation suite, retrieval gold set, negative-result reports, regression tests |
| Limitations | Published recall, latency, cost, coverage, and self-review caveats |

For a bank, the useful part is not "the model is smart." It is "the model's output can be tied to a source, re-performed, and challenged." That is the same instinct behind a validation workpaper.

## What I would do next

1. **Independent human review.** Recruit two or three credit-risk or financial-analysis reviewers, blind a sample of claims, and publish the disagreement rate. Without this, the 95% claim-support figure is only a self-review.
2. **Latency and cost.** P95 of 417 seconds and 4.3M tokens are not acceptable for interactive use. The next step is caching, smaller models for extraction, parallel tool calls, and a hard cost budget per answer.
3. **Credit-risk vertical.** The new `examples/credit-risk` demo reuses the corpus, passage, package, and verifier layers on synthetic policy and model-validation documents. The next step is an independent review of that vertical and a larger question set.
4. **Retrieval.** The evidence-page check reaches page hit@5 0.920 and snippet hit@5 0.907; the full-filing check reaches 0.850 and 0.779 on 61 parsed PDFs. The next step is top1 reranking, a real held-out split, and table-aware retrieval for domain/earnings questions, not larger fixed chunks.
5. **Coverage.** Foreign issuers, earnings calls, and cross-currency ADS bridges are still incomplete. Coverage should be described explicitly rather than implied.
6. **Packaging.** The verifier is already separable. The next step is a small CLI distribution that a reviewer can run without installing the full application.

## Reproduce

With the project environment installed:

```powershell
# Offline package check, no model or network
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json

# Credit-risk vertical demo, no model or network
python examples/credit-risk/demo.py demo

# Full evaluation (requires configured model API for the model-using questions)
python -m filingcopilot evaluate --output artifacts/evaluation.json
```

The recorded evaluation data and caveats are in [`docs/evaluation.md`](evaluation.md). The research-package protocol is in [`docs/research-package-protocol.md`](research-package-protocol.md).

---

*FilingCopilot is an independent engineering project. It is not investment advice, not a production credit-decision system, and not a certified model.*
