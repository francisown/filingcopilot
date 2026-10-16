# Public release checks

Checks performed before publishing this source:

- Git history was searched for tracked `.env`, PEM and key files. Only the
  empty `.env.example` template is tracked; placeholder configuration strings
  are not credentials.
- Tracked text artifacts were scanned for workstation paths; known absolute
  paths were replaced with `<workspace>` or `<user-home>`.
- The default SEC user-agent is empty. Each operator supplies their own contact
  string through `FC_SEC_USER_AGENT` before online requests.
- `docker compose config` was validated with an empty environment file so no
  local credentials were rendered into the release check.
- CI runs tests, lint, UI checks, MCP smoke checks, the evaluation gate, wheel
  builds and wheel-level validation on Windows and Ubuntu.

These checks describe repository hygiene only; they do not certify the accuracy
of research output. See [evaluation limits](evaluation.md).
