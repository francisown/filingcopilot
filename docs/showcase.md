# Project overview

FilingCopilot searches company filings, calculates financial indicators and saves
the evidence behind each answer. The browser, CLI, REST API and MCP server use the
same research service.

## Try the offline example

Install the project using the [installation guide](installation.md), then run:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

The example needs neither network access nor a model key. On macOS and Linux, use
`.venv/bin/python`. To inspect a new answer in the browser:

1. Start the workspace with `start.ps1 -Port 8159`.
2. Select Apple / AAPL and **Data only**.
3. Ask for FY2025 revenue and net income.
4. Open a citation, inspect the reporting period and export the research package.

## Follow a request through the code

| Module | Responsibility |
|---|---|
| [models.py](../src/filingcopilot/models.py) | Facts, claims, research requests and saved answers. |
| [service_research.py](../src/filingcopilot/service_research.py) | Research lifecycle, budgets, previews and saved results. |
| [service_execution.py](../src/filingcopilot/service_execution.py) | Dispatch by request type, prepare evidence and run the selected workflow. |
| [tools.py](../src/filingcopilot/tools.py) | Evidence lookup and financial calculations. |
| [verification.py](../src/filingcopilot/verification.py) | Citation, scope, numeric and completeness checks; accepts a semantic-review callback. |
| [audit.py](../src/filingcopilot/audit.py) | Research-package export and offline checks, including optional comparison with cached source files. |

The [architecture](architecture.md) describes how retrieval, market data and the
evidence ledger fit together. The [standalone verifier](independent-verifier.md)
packages the audit modules without the research agent.

## Evaluation

Regression tests cover calculation rules, source and date checks, cancellation,
provider failures and package compatibility. Retrieval experiments use a fixed
200-question set. Model experiments record delivery, latency and token use separately.

See [evaluation results](evaluation.md) for the datasets and denominators, and
[release changes](release-2026-10-18.md) for the latest fixes. Independent human
review of complete answers remains incomplete. Offline consistency checks cover
the recorded evidence and calculations; they do not reassess narrative meaning.
