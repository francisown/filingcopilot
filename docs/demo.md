# Local demonstration

This walkthrough uses the bundled Apple filings. Follow the
[installation guide](installation.md), then start the workspace:

```powershell
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

1. Select Apple / AAPL, a knowledge date of `2026-10-08`, and **Data only**.
2. Ask `What were Apple's FY2025 revenue and net income?`.
3. Open a citation and check the filing, reporting period, currency and amount.
4. Export the research package and verify it:

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package package.json
```

For raw-source checks, supply a trusted cache as described in the
[verifier instructions](independent-verifier.md). The verifier does not download
missing files.

To try a causal question such as `Why did revenue change?`, configure research and
review models first. New filing downloads also require network access and a contact
string in `FC_SEC_USER_AGENT`. See [configuration](../README.md#enable-live-filings-and-ai-research).
