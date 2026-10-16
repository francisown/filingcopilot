# FilingCopilot: project showcase

This page is the fastest way to review the project as an engineering portfolio piece.
It is deliberately narrower than the full user guide: start with one traceable answer,
then inspect the boundaries that make the answer reproducible.

## The one-line thesis

FilingCopilot is an evidence-first financial research agent: numeric claims are rendered
from typed facts and calculations, narrative claims carry source references, and a saved
research package can be replayed by a standalone verifier without a model call.

The project is a local research tool and an engineering study. It does not claim to be a
licensed market-data product, an investment adviser, or proof that a narrative is true.

## A ten-minute, no-key demo

The bundled corpus lets a reviewer try the deterministic path before configuring a model:

```powershell
.\setup.ps1
$env:FC_EMBEDDING_MODE = "off"
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

The command should produce a consistent verification report without network access or an
API key. To see the browser workspace, keep the environment variable in the same terminal
and run:

```powershell
.\start.ps1 -Port 8159
```

Then choose **Apple / AAPL**, select **Data only**, ask for FY2025 revenue and net income,
open a citation, and export the research package. The point of this path is to see the
relationship between an answer, its evidence, and its calculation inputs.

On macOS or Linux, replace the interpreter path with `.venv/bin/python` and start with:

```bash
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

## What to inspect in the code

Follow one request through these boundaries instead of reading the whole repository:

1. [`models.py`](../src/filingcopilot/models.py) defines typed facts, claims, requests, and
   saved answer contracts.
2. [`service_research.py`](../src/filingcopilot/service_research.py) coordinates a research
   request while preserving its knowledge date and status.
3. [`service_execution.py`](../src/filingcopilot/service_execution.py) turns tool results
   into bounded evidence and calculations.
4. [`audit.py`](../src/filingcopilot/audit.py) collects the evidence ledger and checks the
   source and period bindings.
5. [`verification.py`](../src/filingcopilot/verification.py) replays the saved package
   independently of the model runtime.

The same service rules are used by the browser, CLI, REST API, and MCP server. Retrieval,
market data, and domain configuration feed the ledger; deterministic gates then decide what
can be rendered as a supported result. The architecture overview explains the full flow in
one diagram: [`architecture.md`](architecture.md).

## What the evaluation does and does not show

The repository keeps frozen retrieval and production-budget records rather than replacing
them with a better-looking run. Those records are useful for finding regressions, but they
are not a claim of financial or semantic accuracy. The current limitations are recorded in
[`evaluation.md`](evaluation.md): retrieval quality is still an active research problem,
human semantic review is incomplete, and offline verification only proves the checks that
were requested.

That distinction is part of the design. A future evaluation should add a small blind set
with independent reviewers and report numeric accuracy, citation support, abstention rate,
latency, and model cost separately. Until that exists, do not describe the existing numbers
as an investment-performance or human-accuracy benchmark.

## Why a company might care

The strongest signals are the problem boundaries and the ability to explain trade-offs:

- evidence and calculations remain inspectable instead of being hidden inside a prompt;
- historical availability is explicit, so later filings cannot silently rewrite an older
  research result;
- the verifier gives downstream systems a small, reproducible artifact contract;
- retrieval candidates can be evaluated and rejected without silently changing runtime
  behavior.

This makes the project most relevant to teams working on financial data, research agents,
RAG evaluation, MCP integrations, or local-first developer tools. It is not intended to
demonstrate multi-tenant deployment or production operations.

## Good next contributions

The highest-value additions are small and observable: fix a reproducible retrieval miss,
add an independently reviewed evaluation case, improve a source adapter, or make one part of
the verifier contract easier to consume. New feature breadth is less useful than a short
trace that another person can run, inspect, and challenge.
