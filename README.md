# FilingCopilot

<p align="center">
  <strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**A local-first research workspace for company filings.** FilingCopilot prepares the relevant SEC sources, retrieves evidence, computes financial figures, and returns answers whose claims can be inspected and exported.

![FilingCopilot workspace](docs/images/workspace.png)

The project is built around three rules:

- **Source before answer.** A question is scoped to a company and knowledge date. If the required filing is not in the local corpus, the preparation layer can fetch supported public filings before retrieval.
- **Evidence before prose.** Facts, calculations, passages, periods, units, source URLs and locators are kept in an evidence ledger. Missing support is reported as a gap instead of being silently filled.
- **Re-checkable output.** A research package contains the inputs, claims, citations, calculations and source hashes. The standalone verifier checks the package offline without the research model or network.

## Latest recorded results

The latest full validation record is the 2026-11-02 140-question run: DeepSeek for research and Mimo for review on the frozen 62-filing snapshot, four workers, as-of 2026-10-08, 3,000s research ceiling, 300s review timeout and 16,384 review tokens. The 100 answerable questions and 40 designed boundary/policy questions are reported separately.

| Check | Result |
|---|---:|
| Answerable research coverage | **95 / 100** |
| Expected insufficient-evidence refusals | **20 / 20** |
| Market-source policy cases | **20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Citation binding on delivered rows | **1.000** |
| Citation binding across all rows | **0.857** |
| Model-backed latency | **226.4 s P50 / 1,051.1 s P95** |
| Reported model tokens | **4,044,735** |
| Numeric oracle | **104 exact matches, 0 mismatches**; 27 claims outside its scope |
| Regression suite | **2,305 passed on the current working tree** |

Five answerable rows delivered partial answers: `semantic-causality-002`, `semantic-causality-008`, `semantic-causality-019`, `semantic-negation-001` and `semantic-negation-006`. Each carries explicit gaps rather than an unverified completion.

These are engineering and workflow measurements, not an independent human accuracy study. External answer-support scores use strict `full / partial / unsupported` labels; their denominators and limitations are in [the evaluation record](docs/evaluation.md). The machine-readable record is the [full report](docs/validation/live-full-deepseek-mimo-2026-11-02-final/report.json), [results](docs/validation/live-full-deepseek-mimo-2026-11-02-final/results.json) and [failure summary](docs/validation/live-full-deepseek-mimo-2026-11-02-final/failures.json).

## How a request is handled

```text
Browser / CLI / REST / MCP
            |
     capability and date scope
            |
 source preparation and filing ingestion
            |
   hybrid retrieval and reranking
            |
 facts, passages and calculations in the evidence ledger
            |
 deterministic checks + independent review
            |
 answer, citations, explicit gaps and optional frozen package
```

Deterministic routes handle annual figures, growth, margins and trends without a model. Research routes use a tool-calling agent for questions that need narrative evidence or multi-step calculations. The service is available through the browser workspace, CLI, REST API and 30 MCP tools.

## Quick start

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

## Enable live filings and research

Copy `.env.example` to `.env` and set values locally. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `FC_SEC_USER_AGENT` | Contact name and email required by SEC requests |
| `FC_API_BASE` / `FC_API_KEY` | OpenAI-compatible provider endpoint and private key |
| `FC_MODEL` | Research model; the checked-in example uses DeepSeek |
| `FC_REVIEW_MODEL` | Separate review model; the checked-in example uses Mimo |
| `FC_RESEARCH_TIMEOUT` | Cooperative research ceiling; the public example is 3,000 seconds |
| `FC_WATCHLIST_TRIGGERS_ENABLED` | Opt-in local watchlist scans; false reports candidates without starting jobs |
| `FC_EMBEDDING_MODE` | `off` for keyword retrieval or `local` for optional local embeddings |
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | Optional provider-specific request metadata |
| `FC_MAX_COST_USD` | Optional ceiling; requires both token prices and is rejected otherwise |

Model requests may incur provider charges. The application does not place trades. New filing preparation requires network access and a compliant SEC user agent; existing local data can be queried offline.

## Documentation

- [Architecture](docs/architecture.md)
- [Evaluation and denominators](docs/evaluation.md)
- [Case study](docs/case-study.md) · [案例研究](docs/case-study.zh-CN.md)
- [Installation](docs/installation.md)
- [User guide](docs/user-guide.md)
- [Research package protocol](docs/research-package-protocol.md)
- [Independent verifier](docs/independent-verifier.md)
- [Security and threat model](docs/security-threat-model.md)
- [External evaluation datasets](evaluation/external/README.md)
- [Contributing](CONTRIBUTING.md)

## Development

```powershell
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

The external benchmark data and local filing cache are not committed. Review the licenses in [evaluation/external/README.md](evaluation/external/README.md) before fetching non-commercial datasets.

FilingCopilot is an independent engineering project. It is not investment advice, a production credit-decision system, or a certified model.
