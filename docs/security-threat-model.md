# Security and threat model

FilingCopilot is a **single-user, local-first** application. It is not a
multi-tenant service and it has no roles or tenant isolation. An optional
single-user `FC_AUTH_TOKEN` guard protects the web/API and streamable MCP HTTP
surfaces when configured;
remote deployments still need TLS, an authenticated proxy and rate limits.

## Deployment boundary

- `filingcopilot serve` defaults to `127.0.0.1`; the MCP HTTP server also binds
  loopback.
- The Docker image must listen on the container interface, so it passes an
  explicit `--allow-non-loopback` flag. Compose still publishes the host port
  on `127.0.0.1`; changing that mapping requires an authenticated reverse proxy.
- Do not expose the HTTP or MCP ports to a LAN or the public internet without
  configuring `FC_AUTH_TOKEN` and putting TLS, an authenticated proxy, request
  limits and per-user controls in front of the service. The built-in token is a
  single shared credential, not a login, role model, tenant isolation, quota or
  CSRF token.
- The browser UI and the API share the same local process. A remote deployment
  requires an external reverse proxy, TLS, authentication, authorization,
  request-size limits, and per-user rate limits.
- SEC filings, PDFs, web pages, provider responses, and model output are
  **untrusted input**. They can contain instructions aimed at the agent or
  HTML/script aimed at a browser.
- API keys are read from the process environment. They are not written into
  research packages or logs; do not put them in prompts, filenames, or URLs.

## Threats

| Threat | Current mitigation | Residual risk |
|---|---|---|
| Accidental remote exposure | Loopback defaults; the CLI refuses a non-loopback bind | A loopback reverse proxy must still add its own authentication and TLS |
| Unauthenticated API/MCP access | Loopback defaults; optional `FC_AUTH_TOKEN` for web/API and streamable MCP HTTP | MCP stdio is process-local; deployments without the token still need an authenticated boundary |
| Prompt injection in filings | Model review and evidence checks are scoped to supplied evidence; no formal instruction/data separation | A malicious filing can still influence a model-generated narrative |
| Tool abuse / destructive actions | Tools operate on the configured local home; intent is bounded by request contracts | No OS sandbox or per-tool capability policy |
| Secret leakage | Environment-only keys; package redaction | Logs, crash dumps, or third-party model providers may retain prompts |
| Malicious PDFs | pypdf/PyMuPDF parsing; download allow-list/path handling | Parser vulnerabilities and resource exhaustion remain possible |
| Stored XSS | Static app uses text rendering for evidence, same-origin CSP, and no inline script/style execution | Untrusted text must never be inserted as HTML; CSP is defense in depth, not a substitute for escaping |
| Cross-user data exposure | Single local home | No tenant boundary; do not share one home across users |

## Operating rules

1. Keep the default loopback bind. If a remote bind is ever added, it must be
   opt-in and paired with authentication.
2. Run under a dedicated OS account with access only to the intended evidence
   home.
3. Treat generated answers as research drafts. Do not execute trades or file
   regulatory documents from them.
4. Review the data licenses before commercial use (see
   [data-licenses.md](data-licenses.md)).
5. Report security issues privately; do not paste API keys or proprietary
   filings into a public issue.

This document is a boundary statement, not a claim that the application is
hardened for hostile multi-user deployment.
