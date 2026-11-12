# Three-minute demo script

A short walkthrough for a reviewer or interviewer. It shows the product, the
evidence path and the limits without needing a live model for the first half.

## Before recording

```powershell
.\setup.ps1
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

Open <http://127.0.0.1:8159/>. Use a fresh browser profile or a private window
so the recording does not contain local bookmarks or account data.

## 0:00–0:30 — What it is

"This is a local SEC filing research workspace. It answers a question, shows the
filing passage or calculation behind every claim, and refuses when the evidence
is insufficient. The same service is exposed through the browser, CLI, REST and
MCP."

Show the workspace and the company selector. Do not start with a chart; start
with the question box.

## 0:30–1:10 — Fast deterministic path

Select **Apple / AAPL**, choose **Data only**, and ask:

> What were Apple's FY2025 revenue and net income?

Point out that the answer and the calculation arrive without a model call. Open
one citation and show the accession, period, unit and source link. This is the
30-second path the product is designed for.

## 1:10–2:10 — Narrative path and evidence

Ask a question that needs a filing passage:

> Why did Microsoft's operating margin change?

While it runs, show the progress stages. When the answer arrives, open the
evidence drawer and point out:

- the answer is short and claim-bound;
- every narrative claim has an evidence ID;
- missing requested parts appear as explicit gaps;
- the model does not get to invent a citation.

If the provider is slow, say so and use a previously exported package instead
of waiting. Do not edit the answer to make the demo smoother.

## 2:10–2:45 — Offline verification

Export the research package, then run:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package <package>.json
```

Show that the verifier runs without the research model or network and checks the
saved evidence, calculations and hashes. State the limit: it proves consistency
of the saved package, not semantic correctness or authorship.

## 2:45–3:00 — Close with the boundary

"The project is strong on evidence binding, reproducibility and deterministic
checks. The open gaps are independent human evaluation, hosted multi-user
security and long-question latency. Those are measured and documented rather
than hidden."

## Optional code path

If the interviewer asks where to read the implementation:

1. `docs/architecture.md` for the data flow and trust boundaries.
2. `src/filingcopilot/agent_execution.py` for the tool loop and stop conditions.
3. `src/filingcopilot/verification.py` for claim binding and withholding.
4. `docs/evaluation.md` for the measured coverage, latency and limitations.
