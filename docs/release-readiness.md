# Release checklist

- Keep `.env`, credentials, local caches and model weights outside the source package.
- Supply online SEC contact details through `FC_SEC_USER_AGENT`.
- Run tests, lint, browser checks, MCP checks and distribution validation.
- Check the source manifest and scan the selected files for credentials.
- Confirm the hosted CI result for the commit being released.

Commands and recorded results are in [release checks](publication-checks.md).
The application defaults to local access; hosted deployment needs separate
authentication and operational validation.
