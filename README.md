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

Latest local run: 2026-11-02, 140 questions, DeepSeek research and Mimo review, four questions processed in parallel. The 100 answerable questions and 40 boundary/policy questions are reported separately.

| What | Result |
|---|---:|
| Answerable questions with a complete answer | **97 / 100** |
| Expected refusals / market-policy cases | **20 / 20 · 20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Delivered claims with a source citation | **100%** |
| Regression tests | **2,307 passed** |

Three of the 100 answerable questions returned partial answers with explicit gaps; the rest returned complete answers. Latency, token counts, the numeric audit and the exact question IDs are in [Evaluation](docs/evaluation.md). These are engineering measurements, not an independent human accuracy study.

**Speed:** numeric questions are usually answered in under a second; long narrative questions call the model and an independent reviewer, so they can take minutes. The latest model-backed P95 was 842 seconds; see Evaluation for the full breakdown.

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
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | Optional provider-specific request metadata |
| `FC_MAX_COST_USD` | Optional ceiling; requires both token prices and is rejected otherwise |

Model requests may incur provider charges. The application does not place trades. New filing preparation requires network access and a compliant SEC user agent; existing local data can be queried offline.

## Documentation

- [Architecture](docs/architecture.md)
- [Evaluation and denominators](docs/evaluation.md)
- [Case study](docs/case-study.md)
- [Installation](docs/installation.md)
- [User guide](docs/user-guide.md)
- [Research package protocol](docs/research-package-protocol.md)
- [Independent verifier](docs/independent-verifier.md)
- [Security and threat model](docs/security-threat-model.md)
- [External evaluation datasets](evaluation/external/README.md)
- [Contributing](CONTRIBUTING.md)

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

The external benchmark data and local filing cache are not committed. Review the licenses in [evaluation/external/README.md](evaluation/external/README.md) before fetching non-commercial datasets.

FilingCopilot is an independent engineering project. It is not investment advice, a production credit-decision system, or a certified model.
