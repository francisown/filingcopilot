# Contributing

Thank you for helping improve FilingCopilot. Small fixes, clearer English, reproducible
bug reports, and improvements to data coverage are welcome.

## Development setup

From the project directory, create a virtual environment and install the core development
dependencies. Local embedding models are optional.

```bash
python -m venv .venv
```

On macOS/Linux:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Set `FC_EMBEDDING_MODE=off` for keyword search. Use `.env.example` as a configuration
reference; never commit real API keys or personal configuration.

## Checks

Run the checks relevant to your change. The examples below use the macOS/Linux Python
path; on Windows, use `.\.venv\Scripts\python.exe`.

```bash
.venv/bin/python -m ruff check src scripts tests
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_mcp_readme_tools.py
.venv/bin/python scripts/build_locale_catalog.py --check
.venv/bin/python scripts/check_retrieval_gold.py
```

UI checks also require Node.js:

```bash
node scripts/check_i18n_ui.cjs
node scripts/check_research_ui.cjs
```

The [CI workflow](.github/workflows/ci.yml) contains the complete list. Tests use isolated
fixtures; live provider and model experiments are separate. For test profiles and recorded
commands, see [test-profiles.md](docs/test-profiles.md).

## Working with sources and languages

- Keep original source text, reporting periods, currencies, and stored inputs intact.
- Preserve old research packages. A new display rule should not rewrite a saved answer.
- Edit the shared English catalogue in `src/filingcopilot/locale-en.json`, then regenerate
  the browser catalogue with `python scripts/build_locale_catalog.py`.
- Keep English as the default and Chinese as an explicit, remembered option.
- Treat frozen questions and expected answers as versioned fixtures. Record failed
  experiments alongside passing ones, with their code version and evaluation scope.

For MCP changes, update the server registry, documented tool list, and smoke checks together.

## Reporting a problem

Use the bug-report template in the repository's Issues tab. Include the operating system,
Python version, company/ticker, selected date, and a minimal reproduction. Share sanitized
logs or screenshots rather than `.env` files, API keys, or credentials.

In a pull request, explain the problem, the resulting behavior, and how you checked it.
If the change affects evidence or calculations, include a concrete example with its source.
