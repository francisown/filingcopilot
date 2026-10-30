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

The latest 140-question run used DeepSeek for research and Mimo for review on a frozen 62-filing snapshot. The 100 answerable questions and 40 designed boundary/policy questions are reported separately.

| Check | Result |
|---|---:|
| Answerable research coverage | **91 / 100** |
| Expected insufficient-evidence refusals | **20 / 20** |
| Market-source policy cases | **20 / 20** |
| Unsupported claims / numeric errors | **0 / 0** |
| Citation binding on delivered rows | **1.000** |
| Model-backed latency | **90.6 s P50 / 174.9 s P95** |
| Reported model tokens | **3,753,566** |
| Internal retrieval span/page/nugget recall@5 | **0.615 / 0.635 / 0.710** |
| Internal retrieval latency | **66 ms P50 / 168 ms P95** |
| Numeric oracle | **116 exact matches, 0 mismatches**; 34 claims outside its scope |
| Regression suite | **1,847 passed in the prior full run; 195 targeted tests passed after the latest changes** |

These are engineering and workflow measurements, not an independent human accuracy study. External answer-support scores use strict `full / partial / unsupported` labels; their denominators and limitations are in [the evaluation record](docs/evaluation.md). The latest machine-readable records are limited to the [full report](docs/validation/live-full-deepseek-mimo-2026-10-30-rerun/report.json), [failure summary](docs/validation/live-full-deepseek-mimo-2026-10-30-rerun/failures.json), and [retrieval snapshot](docs/validation/retrieval-diversity-2026-10-29.json).

The latest targeted weak-case check covered nine previously difficult narrative questions: **4 answered / 5 partial**, with zero unsupported claims, numeric errors, runner errors or deadline timeouts. It is recorded with its limits in [the evaluation record](docs/evaluation.md) and is not a replacement for the full 91/100 result.

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
