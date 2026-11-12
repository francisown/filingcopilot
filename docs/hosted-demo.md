# Hosted demo boundary

FilingCopilot is currently a single-user local application. The browser, REST
API and MCP HTTP surface share one optional bearer token; there is no login,
tenant isolation, role system or per-user quota. Do not expose the process
directly to the public internet.

This page is the minimum checklist for a controlled remote demo. It is a
deployment boundary, not a claim that the application is production-ready.

## Already present

- Optional shared `FC_AUTH_TOKEN` guard for the web/API and streamable MCP HTTP
  surface; a missing token is a local-only mode.
- Every HTTP response carries `X-Request-ID`; an invalid caller value is replaced
  with a generated ID. `FC_ACCESS_LOG=true` emits one JSON line per request with
  request ID, method, path, status and duration only.
- Security headers, a restrictive content-security policy and no wildcard CORS.
- Runtime identity checks that ask the operator to restart after code changes.
- Bounded model retries, provider failover, a research deadline and optional
  token/cost ceilings.
- Request-scoped evidence ledgers and an offline package verifier.

## Required before a remote demo

1. **TLS and reverse proxy.** Terminate HTTPS at a proxy, forward only the
   application port, and keep the app bound to the container/private network.
2. **Identity.** Put an authenticated proxy or SSO in front of the app. Do not
   treat the shared token as a user identity.
3. **Per-user budget.** Enforce a daily request and token/dollar budget outside
   the shared `FC_MAX_COST_USD`; a single global ceiling is not a user quota.
4. **Request IDs.** The application now generates or accepts `X-Request-ID` and
   returns it on every response. Still add it to the reverse-proxy access log and
   propagate it to upstream errors.
5. **Audit log.** `FC_ACCESS_LOG` records timestamp, request ID, route, status and
   duration. Add actor and model/tool counts at the proxy or app boundary. Do not
   log filing contents, prompts, API keys or reviewer labels by default.
6. **Rate limits.** Apply per-identity and per-IP limits at the proxy, with a
   separate stricter limit for model-backed research and package export.
7. **Tracing.** Export stage spans (retrieval, model, review, verification,
   export) with request IDs. Keep prompts and extracted text out of trace tags.
8. **Failure policy.** Return a bounded error with a request ID; never convert a
   provider failure into a confident answer or a silent empty result.
9. **Isolation.** Use one corpus/home per demo tenant or reset the demo home on
   a schedule. A shared SQLite file is not tenant isolation.
10. **Kill switch.** Add a flag that stops new model-backed jobs while preserving
    health, history and the offline verifier.

## Demo acceptance

- A cold request returns a request ID and a health/version identity.
- An unauthenticated request is rejected before any model or filing work.
- A model timeout produces an explicit gap or error, not a fabricated answer.
- Logs can reconstruct one request without exposing prompts or secrets.
- The exported package still verifies offline with the network blocked.

Until these checks are implemented and tested, the published deployment should
remain a local workspace or a private, authenticated demo behind a proxy.
