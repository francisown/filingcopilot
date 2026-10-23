# Application mapping — project evidence to target roles

This document maps FilingCopilot evidence to the Singapore roles in the market list. It is **project evidence**, not employment experience. Do not claim years of production experience that the project does not show; frame it as “built and evaluated” rather than “operated at scale.”

The project evidence is:

- Python/FastAPI service, LangGraph agent, SQLite FTS5 + optional local embeddings.
- 30 MCP tools; browser, CLI, REST, and MCP on one research service.
- Evidence ledger, deterministic numeric/narrative gates, semantic review, frozen package, offline verifier.
- 140-question bilingual evaluation; 116 exact SEC/arithmetic matches with 0 mismatches; 1,718 tests passing now (1,656 at the recorded source).
- Published weaknesses: self-reviewed, no independent human gold, P95 417s, 4.3M tokens; exact-passage Recall@5 is 0.430 after the coverage rerank (0.255 lexical baseline), with growth/competition still weak.

## OCBC — AI Engineer (Agentic AI)

**JD signal:** Python/Java, LangChain, RAG, LoRA/QLoRA, distributed training.

**Resume bullets**
- Built a LangGraph research agent that plans tool calls, retrieves filing passages, and submits every claim to deterministic and semantic verification; exposed the same service through browser, CLI, REST, and 30 MCP tools.
- Implemented hybrid retrieval (SQLite FTS5 + optional local embeddings), then added a deterministic coverage rerank that raised exact-passage Recall@5 from 0.255 to 0.430 (MRR 0.142 → 0.245) on a frozen 200-question gold set; kept the negative cross-encoder result.
- Built an offline verifier that recomputes package hashes, document hashes, and quote spans without the model or network.

**Cover letter (2 sentences):** I built an agentic research system where the hard part is not the prompt but the evidence: every claim is tied to a source and can be re-checked offline. I am looking to apply that discipline to OCBC’s agentic AI work, especially in credit and risk workflows.

**Evidence to show:** `docs/case-study.md`, `docs/architecture.md`, `examples/credit-risk/`.

**Honest gap:** Java, LoRA/QLoRA fine-tuning, and distributed training are not demonstrated. Close with a small fine-tuning experiment or a Java service, and say so.

## DBS — MLOps / AI Engineer (fraud / agent)

**JD signal:** Python, MLOps, Kubernetes, AWS, Agent.

**Resume bullets**
- Built a reproducible agent evaluation harness: 140 bilingual questions, frozen source snapshot, per-question packages, audit replay, and CI on Windows/Ubuntu × Python 3.10/3.12.
- Added regression coverage for tool-argument normalization, currency-direction errors, document-scope overreach, and duplicate facts; kept failures visible in the repository.
- Designed a credit-risk vertical demo that reuses the evidence layer on policy, validation, and portfolio documents.

**Cover letter:** I care about agent reliability after the demo: reproducible packages, regression tests, and a verifier that a risk reviewer can run. I would like to bring that operating discipline to DBS’s fraud and agent platforms.

**Evidence:** `docs/evaluation.md`, `.github/workflows/ci.yml`, `examples/credit-risk/`.

**Honest gap:** No production Kubernetes/AWS deployment or fraud-domain experience. A small containerized deployment with observability would close part of it.

## Airwallex — AI Engineer (agentic payments)

**JD signal:** Python, LLM, Agent, RAG, risk.

**Resume bullets**
- Built a tool-using agent with explicit budgets (rounds, tool calls, time, review) and a claim-level verification layer for financial answers.
- Implemented citation-bound retrieval over SEC filings and a synthetic credit-risk corpus; every answer exports a SHA256 package and verifies offline.
- Published an honest evaluation: 81/100 research questions complete, 116 numeric matches with 0 mismatches, and a coverage rerank that raised Recall@5 from 0.255 to 0.430, with growth/competition still weak.

**Cover letter:** Agentic payments need the same property as credit research: the action must be traceable to a policy or a source, and it must be reproducible after the fact. I built that evidence layer and want to apply it to payment-risk workflows.

**Evidence:** `docs/case-study.md`, `docs/independent-verifier.md`.

**Honest gap:** No payments, ledger, or real-time transaction experience. Add a small transaction-risk prototype before applying.

## Mastercard — AI Engineer (real-time risk / agent)

**JD signal:** Python, LLM, real-time inference, Agent.

**Resume bullets**
- Separated deterministic numeric checks from model-assisted narrative review, so low-latency calculations do not depend on an LLM.
- Built a frozen evidence package and standalone verifier that a second-line reviewer can run without the agent or model.
- Mapped the evidence model to SR 11-7-style concerns: conceptual soundness, data lineage, reproducibility, human review, and limitations.

**Cover letter:** Real-time risk decisions cannot wait for a model to explain itself. I built a system that keeps the numeric path deterministic and uses the agent only where narrative judgment is needed, with an offline evidence check.

**Evidence:** `docs/case-study.md` (“Governance and model-risk angle”), `docs/evaluation.md`.

**Honest gap:** No streaming/low-latency production system. The P95 417s is a clear limitation; a cache and a smaller extraction model would be the next step.

## Sonar — AI Engineer (LLM verification)

**JD signal:** LLM verification, evaluation, false-positive/false-negative control.

**Resume bullets**
- Built a layered verifier: deterministic numeric gates, currency-direction checks, document-scope checks, semantic review, and an offline package verifier.
- Replayed known false accepts (USD/FX direction, document-wide absence) and converted them into regression tests that now reject the same claims.
- Published negative retrieval results and kept the failing cases in the repository instead of tuning them away.

**Cover letter:** Verification is the product, not a feature bolted onto a demo. I built a claim-level evidence layer, preserved the failures, and made the review reproducible; that is the work I want to do at Sonar.

**Evidence:** `docs/independent-verifier.md`, `docs/evaluation.md` (“Failure taxonomy” in the case study).

**Honest gap:** No formal verification or static-analysis background. The project is empirical verification, not proof.

## NCS — AI Engineer (RAG / enterprise)

**JD signal:** Python, RAG, Agent, cloud, deployment.

**Resume bullets**
- Built a local-first RAG service with SQLite FTS5, optional local embeddings, a query rewrite layer, and a section-aware retrieval path.
- Exposed the same evidence rules through browser, CLI, REST, and 30 MCP tools; packaged the verifier separately from the research agent.
- Added Docker, CI, and 1,700+ tests, including Windows-specific installation checks.

**Cover letter:** Enterprise RAG is mostly about the unglamorous parts: source control, reproducibility, permissions, and review. I built those parts around a working research agent and would like to do it for enterprise deployments.

**Evidence:** `README.md`, `docs/architecture.md`, `.github/workflows/ci.yml`.

**Honest gap:** No SSO, RBAC, or multi-tenant deployment. Add a small access-control design note before the interview.

## Citi — AI / ML Engineer

**JD signal:** Python, MLOps, cloud, LLM.

**Resume bullets**
- Built an evidence and verification layer for an LLM research agent, with source hashes, claim-level review, and offline replay.
- Designed a credit-risk vertical demo that maps policy clauses, validation findings, and portfolio tables to the same evidence model.
- Wrote the evaluation and limitations documentation, including self-review caveats and a negative retrieval result.

**Cover letter:** My background is credit risk, and I built this project to connect that domain to practical LLM engineering. I am interested in Citi’s AI/ML work where model risk, evidence, and delivery meet.

**Evidence:** `docs/case-study.zh-CN.md`, `examples/credit-risk/`.

**Honest gap:** Cloud MLOps and large-scale deployment are not demonstrated. The project is local-first; be explicit about that.

## How to use this document

- Pick 2–3 roles, not all 7. Tailor the resume bullets to the JD.
- Bring one artifact to the interview: the case study, the credit-risk demo, or the verifier. Do not bring the whole README.
- Lead with the evidence layer and the domain mapping; the agent framework is secondary.
- State the limitations before the interviewer finds them. The self-review, Recall@5 0.430 (up from 0.255), and P95 417s are known; present them as engineering judgment, not hidden weaknesses.
