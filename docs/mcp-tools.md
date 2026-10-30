## MCP integration

The `ask_financial_question` tool defaults to English. Chinese clients can pass
`language: "zh"`. This selects newly generated explanations and numeric wording,
while original source excerpts, evidence identifiers and saved packages retain their contents.
The [English guide](../README.md) covers installation and browser usage;
the [Chinese guide](../README.zh-CN.md) is available separately.

The **Connect assistants** page generates a configuration using your installation paths.
For STDIO, replace `<workspace>` with your own project directory:

```json
{
  "mcpServers": {
    "filingcopilot": {
      "command": "<workspace>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "filingcopilot", "--home", "<workspace>\\.filingcopilot", "mcp"],
      "cwd": "<workspace>"
    }
  }
}
```

The service uses FastMCP from the official MCP Python SDK and exposes these tools.
`scripts/check_mcp_readme_tools.py` compares this list with the server registry:

<!-- MCP_TOOL_LIST_BEGIN -->
`get_watchlist`, `get_company_updates`, `get_cashflow`, `get_ttm_financial_fact`, `get_ttm_margin`, `get_stock_valuation`, `get_provider_ttm_valuation`, `get_historical_valuation`, `prepare_quarterly`, `get_quarterly_overview`, `get_quarterly_fact`, `get_quarterly_sequence`, `get_statement`, `find_stock`, `get_peer_candidates`, `get_financial_trend`, `compare_companies`, `get_stock_market`, `find_company`, `prepare_company`, `list_filings`, `get_financial_fact`, `get_company_overview`, `get_fact_versions`, `export_research_package`, `audit_research_package`, `search_passages`, `calculate`, `ask_financial_question`, `valuation_scenarios`
<!-- MCP_TOOL_LIST_END -->

The server also provides the `filingcopilot://catalog` resource and the `company_research`
prompt. `get_company_overview` returns two annual periods with facts and source-bound
growth and margin calculations. Use `find_company` to choose the company identity,
then `prepare_company` to download and index its supported filings.
`ask_financial_question` also accepts `company` and `auto_acquire`.

Calculation and disclosure tools return only the evidence IDs bound to that
result in `observed_evidence`. `observed_evidence_count` and
`omitted_observed_evidence_ids` make the compact projection explicit; this
prevents a multi-step client from receiving the entire accumulated ledger on
every call. Use `search_passages` or an exported package when the omitted
items are needed.

Initial acquisition and Agent research can take longer than a minute; allow enough
time in your MCP client. Export and audit calls use a saved research ID and do not
call a model. For `calculate`, pass observed local fact IDs. Use the dedicated tools
for nested trailing flows and margins; the server rechecks sources and dates.

The default MCP transport is stdio. Streamable HTTP binds to loopback only unless
`--allow-non-loopback` is supplied. Any non-loopback MCP HTTP bind also requires
`FC_AUTH_TOKEN` as a Bearer token; this is one shared local credential, not user
or tenant isolation. Put TLS, identity and rate limits in a reverse proxy for
remote use.

When adding or removing a tool, update the server registry, the expected set in
`scripts/smoke_mcp.py` and the list above together. Run the smoke checks for both transports.

For streamable HTTP, run `filingcopilot mcp --transport streamable-http --port 8124`
and connect to `http://127.0.0.1:8124/mcp`. MCP, the browser and the CLI share the same
research service and Agent-mode model usage rules.
