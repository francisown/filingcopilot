# 3-minute demo script

**Goal:** show the evidence layer, not a UI tour. The viewer should remember one sentence: *every answer can be traced to a source and re-checked offline.*

## Pre-flight (do this before the call)

```powershell
# From the repository root, once
.\setup.ps1

# Start with keyword retrieval; no model key needed for the deterministic path
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

Open <http://127.0.0.1:8159/> in a clean browser window. Have a terminal open at the repository root.

If the port is busy, use `-Port 8160`. If PowerShell blocks the script, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Port 8159
```

## 0:00–0:20 — Frame the problem

Say:

> Most RAG demos show a fluent answer. In finance, the answer is the easy part. The hard part is proving where every number came from, and letting a reviewer re-check it after the model or the machine changes. This project is built around that second part.

## 0:20–1:10 — Deterministic path, no model

1. Select **Apple / AAPL**.
2. Choose **Data only**.
3. Ask: **“What were Apple's FY2025 revenue and net income?”**
4. Open the citation.
5. Point at the reporting period, currency, units, and source locator.
6. Say:

> This path did not call a model. The number comes from the filing corpus and a deterministic calculation. The citation is a pointer to a stored passage, not text the model made up.

## 1:10–1:50 — Agent path, with gaps

1. Switch to **AI research**.
2. Ask: **“What are the main businesses and risks disclosed in the latest annual report?”**
3. While it runs, say:

> The agent plans the work, retrieves passages, calls financial tools, drafts claims, and submits them to verification. It also has a budget: rounds, tool calls, time, and review. When the evidence is incomplete, it should show a gap rather than invent completeness.

4. Open the answer. Point at the citation and the review status. If there is a gap, point at it.

## 1:50–2:30 — Freeze and verify offline

1. Export the research package.
2. In the terminal:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package <path-to-package.json>
```

3. Show `consistent`.
4. Say:

> The package contains the question, claims, citations, calculations, source hashes, and a package hash. The verifier does not need the agent, the model, or the network. If a source changes, the document hash fails. If a quote is edited, the span check fails. If the package is edited, the package hash fails.

5. Optional: run the credit-risk demo:

```powershell
python examples/credit-risk/demo.py demo
```

> The same evidence layer runs on a synthetic credit policy and a model-validation report. That is the credit-risk mapping: clause-level citations, document hashes, a frozen workpaper, and an offline re-performance.

## 2:30–3:00 — Close with the limitation

Say:

> The honest limitation: the 140-question evaluation is self-reviewed, there is no independent human gold standard, and model-using questions had a P95 of 417 seconds. Retrieval Recall@5 is 0.430 after the coverage rerank (0.255 lexical baseline), and growth/competition are still weak. The project is strong on evidence integrity and honest on accuracy. That is the part I would want a reviewer to challenge.

## 30-second version

> FilingCopilot answers questions about SEC filings, but the product is the evidence layer: every number is tied to a filing, period, currency, unit, and locator; every answer can be frozen into a SHA256 package and re-checked offline without the model. The deterministic path needs no LLM. The agent path is evaluated on 140 bilingual questions, and the limitations are published. I also mapped the same layer to a synthetic credit-risk corpus: policy clauses, validation findings, and portfolio tables.

## If the live demo fails

- **No model key:** use the **Data only** path and the bundled `examples/annual-facts.json` verifier. Do not apologize; the deterministic path is the point.
- **No network:** the offline verifier and credit-risk demo still work.
- **UI issue:** switch to the CLI:
  ```powershell
  .\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
  python examples/credit-risk/demo.py demo
  ```
- **Time is short:** skip the agent path and spend the time on the verifier and the credit-risk demo.

## Do not

- Do not claim production readiness, universal accuracy, or investment advice.
- Do not show the 417-second P95 live unless the audience asks about latency.
- Do not call the 95% claim-support figure an independent accuracy score.
- Do not read the README aloud; show the evidence.
