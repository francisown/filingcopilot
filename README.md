# FilingCopilot

<p align="center">
  <strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**Ask a question about a U.S. company filing. Get an answer you can check.**

FilingCopilot is a research workspace for SEC filings that runs on your machine. It finds the relevant annual or quarterly report, pulls the exact passages, calculates the financial figures, and shows the source behind every claim. If the filing does not support an answer, it says so instead of guessing.

![FilingCopilot workspace with an AAPL price chart](docs/images/workspace-chart.png)

*The workspace: price history, filing research and source checks in one place.*

## Five-minute reading path

1. [Architecture](docs/architecture.md) — data flow, evidence ledger and trust boundaries.
2. [`agent_execution.py`](src/filingcopilot/agent_execution.py) — tool loop, budgets and stop conditions.
3. [`verification.py`](src/filingcopilot/verification.py) — claim binding, numeric gates and withholding.
4. [Evaluation](docs/evaluation.md) — measured coverage, latency, tokens and limitations.
5. [Demo script](docs/demo-script.md) — a three-minute walkthrough; [human-review protocol](docs/human-evaluation-protocol.md) for the still-pending independent study.

## What it does

- Answers questions about revenue, profit, margins, cash flow, risks and the wording of a filing.
- Numbers are handled by fixed rules and calculations; an AI research agent is used only when a question needs narrative evidence or multi-step reasoning.
- Returns a short answer, the cited passages, code-calculated figures and explicit gaps.
- Can export a complete research package that a standalone verifier checks offline.

## What you can ask

- "What were Apple's FY2025 revenue and net income?"
- "Why did Microsoft's operating margin change?"
- "What regulatory, demand and customer-concentration risks did TSMC disclose?"
- "Did this filing explicitly deny a demand decline?"

## Why it is different

- **Every answer shows its sources.** Each delivered claim points to a filing passage or a code calculation.
- **It says when evidence is missing.** No fluent guess; the answer carries a visible gap or a refusal.
- **You can re-check it offline.** The exported package can be verified without the research model or network.

## Latest snapshot

Latest local run: 2026-11-11, 140 questions, DeepSeek research and Mimo review, four questions processed in parallel. The 100 answerable questions and 40 boundary/policy questions are reported separately.

| What | Result |
|---|---:|
| Answerable questions with a complete answer | **96 / 100** |
| Expected refusals / market-policy cases | **20 / 20 · 20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Delivered claims with a source citation | **100%** |
| Regression tests | **2,358 passed** |

Four of the 100 answerable questions returned partial answers with explicit gaps (`causality-012/015/019`, `negation-001`); the rest returned complete answers. Latency, token counts, the numeric audit and the exact question IDs are in [Evaluation](docs/evaluation.md). These are engineering measurements, not an independent human accuracy study.

**Speed:** numeric questions are usually answered in under a second; long narrative questions call the model and an independent reviewer, so they can take minutes. The pre-change full run had a model-backed P95 of 842 seconds. The post-change 140-question rerun reached **P50 155 s / P95 587 s** with 96/100 answerable rows and no review truncation; the faster 8,192-only variant was rejected because it silently dropped claims. See [Latency budget](docs/latency-budget.md) for the trade-off.

## Try it in 60 seconds

Python 3.10 or later is required. The bundled filing example works without an API key or GPU.

### Windows PowerShell

```powershell
.\setup.ps1
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

Open <http://127.0.0.1:8159/> and select **Apple / AAPL** with **Data only**. Try the bundled package verifier:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e "."
cp .env.example .env
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

The complete setup and troubleshooting steps are in [docs/installation.md](docs/installation.md).

## What you get

For every answer:

- a short conclusion;
- the cited filing passages and/or code-calculated figures;
- explicit gaps for requested parts that are missing;
- an optional research package that can be exported and checked offline.

## How it works

```text
Browser / CLI / REST / MCP
            |
     capability and date scope
            |
 source preparation and filing ingestion
            |
   hybrid retrieval and reranking
            |
 facts, passages and calculations stored with their sources
            |
 fixed checks + independent review
            |
 answer, citations, explicit gaps and an optional research package
```

Fixed rules handle annual figures, growth, margins and trends without a model. Research questions use a tool-calling agent when they need narrative evidence or multi-step calculations. The service is available through the browser workspace, CLI, REST API and 30 MCP tools.

## Who it is for

- Analysts, investors, students and anyone who needs to read filings carefully.
- Local or private workflows: data stays on your machine except the model/API calls you configure.

It is not investment advice, a trading system, a production credit-decision system, or a replacement for reading the filing and independent human review.

## Enable live filings and research

Copy `.env.example` to `.env` and set values locally. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `FC_SEC_USER_AGENT` | Contact name and email required by SEC requests |
| `FC_API_BASE` / `FC_API_KEY` | OpenAI-compatible provider endpoint and private key |
| `FC_MODEL` | Research model; the checked-in example uses DeepSeek |
| `FC_REVIEW_MODEL` | Separate review model; the checked-in example uses Mimo |
| `FC_RESEARCH_TIMEOUT` | Overall time limit for one research run; the public example is 3,000 seconds |
| `FC_WATCHLIST_TRIGGERS_ENABLED` | Optional: scan saved tickers and queue research only when enabled |
| `FC_EMBEDDING_MODE` | `off` for keyword retrieval or `local` for optional local embeddings |
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | Optional provider-specific request metadata. OpenCode Go requires a stable `FC_SESSION_ID` (`x-opencode-session`) and its own base URL. |
| `FC_MAX_COST_USD` | Optional ceiling; requires both token prices and is rejected otherwise |

Monitoring, digest, SMTP/Slack and brief-budget variables are documented in
[Monitor v1](docs/monitoring-v1.md).

Model requests may incur provider charges. The application does not place trades. New filing preparation requires network access and a compliant SEC user agent; existing local data can be queried offline.

For an OpenCode Go subscription, use
`FC_API_BASE=https://opencode.ai/inference/go/openai/v1`, a stable
`FC_SESSION_ID`, and the HTTP model ids (`deepseek-v4.1-flash`,
`mimo-v2.6-flash`). The pay-as-you-go base URL and the free-tier models do not
accept a Go key from an external client. See [OpenCode Go](docs/opencode-go.md).

## Disclosure monitoring (Monitor v1)

Monitor v1 turns saved companies into an evidence-first review queue: SEC
10-K / 10-Q / 8-K discovery, structured and section-level diffs, deterministic
8-K item classification, one-page cited briefs, and human review with versioned
approvals.

```powershell
.\.venv\Scripts\python.exe -m filingcopilot monitor-scan AAPL MSFT
.\.venv\Scripts\python.exe -m filingcopilot monitor-cases
.\.venv\Scripts\python.exe -m filingcopilot monitor-review <case-id> approve --brief-version 1
.\.venv\Scripts\python.exe -m filingcopilot monitor-replay AAPL `
  --start 2025-01-01 --end 2025-12-31
.\.venv\Scripts\python.exe -m filingcopilot monitor-digest --pdf digest.pdf
.\.venv\Scripts\python.exe -m filingcopilot monitor-verify case-package.json
```

Open **Disclosure monitor** in the browser workspace to review, edit, approve or
dismiss cases. Live discovery requires `FC_SEC_USER_AGENT`. Replay, digest,
SMTP/Slack, brief-budget and scoring details are in
[Monitor v1](docs/monitoring-v1.md); OpenCode Go setup is in
[OpenCode Go](docs/opencode-go.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Evaluation and denominators](docs/evaluation.md)
- [Latency and cost budget](docs/latency-budget.md)
- [Case study](docs/case-study.md)
- [Installation](docs/installation.md)
- [User guide](docs/user-guide.md)
- [Research package protocol](docs/research-package-protocol.md)
- [Independent verifier](docs/independent-verifier.md)
- [Security and threat model](docs/security-threat-model.md)
- [Hosted demo boundary](docs/hosted-demo.md)
- [Demo script](docs/demo-script.md)
- [Human evaluation and pilot protocol](docs/human-evaluation-protocol.md)
- [Disclosure monitoring specification](docs/monitoring-v1.md)
- [OpenCode Go API](docs/opencode-go.md)
- [External evaluation datasets](evaluation/external/README.md)
- [Contributing](CONTRIBUTING.md)

## Development

```bash
python -m pip install -e ".[dev,semantic,evaluation]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

The full extras match CI. A core `.[dev]` install also collects the suite; tests
that need pandas, fastembed or pypdf skip explicitly when those extras are absent.
After changing the suite, keep the published count in sync with
`python scripts/update_validation_counts.py <count> --write` and verify it with
`--check`. The external benchmark data and local filing cache are not committed. Review the licenses in [evaluation/external/README.md](evaluation/external/README.md) before fetching non-commercial datasets.

FilingCopilot is an independent engineering project. It is not investment advice, a production credit-decision system, or a certified model.
