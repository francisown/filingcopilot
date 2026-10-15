# FilingCopilot

<p align="center">
  <strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**Research stocks with financial data, price charts, and AI answers linked to original filings.**

FilingCopilot runs on your computer and brings company filings, market data, and research
tools into one workspace. Look up a company, explore its financial performance, compare it
with other companies, and ask focused questions. Open a citation to read the original
disclosure, or export the facts and calculations behind an answer for later review.

It is useful for individual investors, financial-analysis students, and developers building
financial research tools. Financial filings primarily cover companies that report to the
US SEC; price-chart coverage is separate and depends on the market-data provider.

![English workspace with a stock chart and financial research controls](docs/images/workspace.png)

*Interface example using bundled filing data and illustrative prices. The chart is not a live quote.*

[Quick start](#quick-start) · [Using the workspace](#using-the-workspace) · [MCP tools](docs/mcp-tools.md) · [Contributing](CONTRIBUTING.md)

## What you can use it for

- Understand a company's business, operating performance, cash generation and disclosed risks.
- View long-term price charts alongside trends in revenue, earnings, and cash flow.
- Compare companies and optionally add SPY or QQQ as a price benchmark.
- Explore how growth, valuation multiples, and discount rates affect a calculation.
- Keep a record of the facts, sources and formulas behind a research result.
- Connect filing search and financial tools to an MCP-compatible assistant.

## Features

| Feature | What it does |
|---|---|
| Company and stock search | Search by name or ticker, choose the correct listing, and download supported SEC filings. |
| Long-term price charts | Switch between one year, five years, full history and other intervals. Hover to inspect dates and prices; view currency, timestamps and adjustment basis. |
| Financial data | Query annual, quarterly, year-to-date and trailing-twelve-month data. Calculate growth, margins and clearly defined cash-flow measures. |
| Financial trends | Explore three- or five-year revenue, earnings and cash-flow charts. Missing years remain missing; click a figure to inspect its source. |
| Company research | Read business and risk disclosures, financial statements, available segment data, and filing revisions. |
| Official updates | Read supported companies' recent official disclosures, dates and source excerpts. Configured models can produce cited summaries. |
| Company comparison | Compare financial figures and price returns over shared dates, with an optional ETF benchmark and current provider TTM valuation. |
| Valuation scenarios | Compare annual and provider TTM P/E, inspect supported historical annual P/E data, and explore editable earnings and cash-flow assumptions. |
| AI research | Ask focused questions or run a six-part company review. Track progress, ask follow-up questions, cancel a task, or reconnect after refreshing the page. |
| Saved research | Maintain a watchlist, reopen results, export research packages and inspect changes between research versions. |
| Offline verification | Recompute saved calculations and check a research package. Provide the matching source files to check their contents against the saved evidence. |
| Integrations | Use the browser, CLI, REST API or 30 MCP tools. |

**Stock charts do not require downloading annual reports first.** Explicit annual numbers,
growth rates, margins and financial trends can run without an LLM. Explanations, generated
announcement summaries and AI company reviews require model configuration. Incomplete
evidence can lead to a partial answer; the interface shows what is still missing.

## Quick start

Requires Python 3.10 or later. Download the source and run these commands in the project
directory. You can start with the bundled filing data **without a model API key or GPU**.
Live prices and new filings require internet access.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "."
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.example .env
}
$env:FC_EMBEDDING_MODE = "off"
```

Start the workspace with keyword search:

```powershell
.\start.ps1 -Port 8159
```

You can also run `.\setup.ps1` to select an available Python 3.10 or later and
install the core dependencies. It reuses a compatible existing `.venv`.
For a new environment, `-PythonExecutable` accepts the full interpreter path.
`-Profile locked` is an optional reproduction of the historical Python 3.10
dependency snapshot, which includes development and embedding packages.
See the [installation guide](docs/installation.md) for these options.

Open [the local workspace](http://127.0.0.1:8159/). Keep the service terminal running;
press `Ctrl+C` in that terminal to stop it. Existing installations can use the start
command directly. The conditional copy preserves an existing `.env`. The embedding
setting above applies to this terminal session; local embeddings are optional.

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e "."
if [ ! -f .env ]; then
    cp .env.example .env
fi
```

Start with keyword search:

```bash
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

Open `http://127.0.0.1:8159/`.

### Try it before configuring a model

1. Choose **Apple / AAPL** and **Data only**.
2. Ask **“What were Apple's FY2025 revenue and net income?”**
3. Open a citation to inspect the reporting period and source.
4. Export the research package, or verify the bundled example:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

The bundled example can be checked without network access. On macOS/Linux, replace the
Python path with `.venv/bin/python`. Live stock charts load separately from this filing demo.

### Enable live filings and AI research

Edit `.env` when you are ready to add online filing downloads or AI-generated analysis.

| Variable | Purpose |
|---|---|
| `FC_SEC_USER_AGENT` | Your real contact name and email, required for online SEC requests. |
| `FC_API_BASE` | An OpenAI-compatible model endpoint supporting tool calls. |
| `FC_API_KEY` | The model provider's access key. |
| `FC_MODEL` | The research model. |
| `FC_REVIEW_MODEL` | The analysis-review model; use a different model where possible. |
| `FC_EMBEDDING_MODE` | `off` for keyword search; `local` for optional local embeddings. |
| `FC_TWELVE_DATA_API_KEY` | Optional independent daily-price fallback, subject to provider access. |

Set the model endpoint, key, and model names to enable **AI research**. This mode sends
the question and selected evidence to your configured provider and may incur model usage
charges. The app does not place trades.

For optional vector search, install `.[semantic]` in the same virtual environment and set
`FC_EMBEDDING_MODE=local`. If you used the Windows quick start, also change that terminal's
`$env:FC_EMBEDDING_MODE` to `"local"` or start a new terminal. First use downloads model files;
keyword search does not need them.

## Using the workspace

1. **Choose your language.** The interface starts in English, including on a Chinese-language browser.
   Choose 中文 in the header to use Chinese; your explicit choice is saved locally.
2. **Find a listing.** Search for a company or ticker such as `AMZN` and choose its market.
   Explore the price chart before preparing financial reports.
3. **Browse company sources.** Open Company Research to read available material or check
   newer disclosures. Inspect statements, quarters, filing versions and source passages.
4. **Ask a focused question.** Select a company and an **As of date**, choose Data only or
   AI research, then start. The date controls which publicly available filings can be used.
   Try “FY2025 revenue and net income” or
   “What are the main businesses and risks disclosed in the latest annual report?”
5. **Compare and explore assumptions.** Select companies, a price interval and an optional
   benchmark. Edit valuation assumptions and inspect their inputs and formulas.
6. **Inspect and save.** Open citations, add a listing to the watchlist, reopen research
   history and export a package for offline checks.

New research uses the selected interface language. Original filing excerpts remain in their
source language, and saved answers keep the language in which they were generated.
Changing the interface does not rewrite an old answer or its exported package.

CLI and MCP research default to English. Select Chinese with `--language zh` in the CLI
or `language: "zh"` in REST/MCP requests. For example:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot ask "FY2025 revenue and net income" --ticker AAPL --mode reference
```

REST research accepts `language: "en"` or `"zh"`. If omitted, a supported
`Accept-Language` header can select the language; callers without either receive English. The MCP `ask_financial_question` tool also accepts `language`.

## Installation and distribution

This project can be installed directly from its source folder using the commands above.
Publishing the project to PyPI is optional; it is not required to run the workspace or
share the source on GitHub. PyPI distributes installable packages; it does not host the
running web application. The installation commands use the source folder rather than
assuming an uploaded `filingcopilot` package exists. See the
[official local-install guide](https://pip.pypa.io/en/stable/topics/local-project-installs/).

## Sources and calculations

Financial numbers come from observed facts and formulas. References retain company,
filing, public date, reporting period, currency and units. Display rounding usually keeps
at most two decimal places; saved inputs and exports keep their original precision.
English amount displays use thousands, millions and billions; Chinese displays use 万 and 亿.

Filing coverage primarily follows SEC public disclosures. US domestic issuers can be
discovered dynamically; foreign-issuer financial extraction uses registered configurations.
Market-data coverage and filing coverage are separate. Historical research respects public
availability, and unavailable quarters, segments or valuation inputs remain explicit gaps.
Full earnings-call material and some cross-currency ADS bridges still need additional sources.

## Technology and documentation

Python and FastAPI serve the application. SQLite FTS5 and optional local embeddings support
retrieval; LangGraph orchestrates research tools. Browser, CLI, REST and MCP share the same
evidence and calculation rules.

- [MCP tools](docs/mcp-tools.md)
- [Architecture](docs/architecture.md)
- [Research package protocol](docs/research-package-protocol.md)
- [Independent verifier](docs/independent-verifier.md)
- [Development and evaluation](docs/evaluation.md)
- [Roadmap](ROADMAP.md)
- [Chinese guide and historical implementation notes](docs/user-guide.md)

## Feedback and contributions

Maintained by [francisown](https://github.com/francisown). Bug reports, clearer examples,
data-source improvements, and documentation contributions are welcome. Use the repository's
Issues tab for feedback and include the ticker, date, and steps needed to reproduce a problem.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and relevant checks.

Project code is available under the [MIT license](LICENSE).
