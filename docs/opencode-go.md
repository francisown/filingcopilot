# OpenCode Go API

FilingCopilot can use OpenCode Console models through the OpenAI-compatible
Inference API. There are two different surfaces:

| Surface | Base URL | Billing |
|---|---|---|
| Console pay-as-you-go | `https://opencode.ai/inference/openai/v1` | Account balance |
| **OpenCode Go subscription** | `https://opencode.ai/inference/go/openai/v1` | Go monthly allowance |

A Go key does **not** work on the pay-as-you-go surface. A request there with a
Go model id returns `402 Insufficient account funds` or `MissingSessionID`.

## Required configuration

```dotenv
FC_API_BASE=https://opencode.ai/inference/go/openai/v1
FC_API_KEY=oc_sk_...
FC_MODEL=deepseek-v4.1-flash
FC_REVIEW_MODEL=mimo-v2.6-flash
FC_SESSION_ID=fc-monitor-<stable-id>
FC_MONITORING_USE_MODEL=true
```

`FC_SESSION_ID` is sent as `x-opencode-session`. It must be stable for a
conversation so OpenCode can route and cache efficiently. The client already
sends `User-Agent: FilingCopilot/1.0`; this is required by the Go docs instead
of a generic SDK user agent.

## Model ids

Use the HTTP API ids, without the `opencode-go/` provider prefix:

- `deepseek-v4.1-flash`
- `mimo-v2.6-flash`

`opencode-go/deepseek-v4.1-flash` is the model id used inside OpenCode config,
not the model id for an HTTP request.

## Why the previous URL failed

1. `FC_API_BASE=https://opencode.ai/inference/openai/v1/chat/completion` is a
   full endpoint, but the OpenAI SDK appends `/chat/completions` again. The
   result is a 404. Use the base URL, not the full endpoint.
2. The Go subscription is served from `/inference/go/openai/v1`, not the
   pay-as-you-go `/inference/openai/v1`.
3. Free-tier chat models are only available from inside OpenCode. Other clients
   receive an error even with a valid key. Use a paid model or a Go plan from an
   external client.

## Verify

```powershell
curl.exe -sS "https://opencode.ai/inference/go/openai/v1/chat/completions" `
  -H "Authorization: Bearer $env:FC_API_KEY" `
  -H "Content-Type: application/json" `
  -H "User-Agent: FilingCopilot/1.0" `
  -H "x-opencode-session: fc-monitor-check" `
  -d "{\"model\":\"deepseek-v4.1-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":32}"
```

A successful Go response reports `usage.opencode.billing_source` as `go`.
