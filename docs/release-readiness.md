# Public release checklist

The 2026-10-11 release check recorded in
[`release-readiness-2026-10-11.json`](../artifacts/README.md)
covers the repository state after the research-package protocol work.

- Git history was searched for tracked `.env`, PEM and key files. Only the
  empty `.env.example` template is tracked; placeholder configuration strings
  are not credentials.
- Tracked text artifacts were scanned for workstation paths and known absolute
  paths were replaced with `<workspace>` or `<user-home>`.
- The default SEC user-agent is empty. Each operator supplies their own contact
  string through `FC_SEC_USER_AGENT` before online requests.
- `docker compose config` was validated with an empty environment file so no
  local credentials were rendered into the release check.
- The CI workflow runs tests, lint, UI checks, MCP smoke checks, the evaluation
  gate, and a wheel build on Windows and Ubuntu.

This checklist does not rotate a provider key or rewrite existing Git history.
If a private working copy ever contained a live key, rotate it with the
provider before publishing and run a dedicated history scrub outside this
repository.
