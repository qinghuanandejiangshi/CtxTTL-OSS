# Responses API transparent proxy

[简体中文](zh-CN/responses-api-proxy.md)

CtxTTL exposes an additive OpenAI-compatible Responses endpoint:

```text
POST /v1/responses
GET  /v1/models
```

The endpoint shares lifecycle projection, deterministic selection, storage, tracing, and identity
rules with the Chat Completions proxy. Responses-specific translation remains in an isolated
adapter; it does not change the compiler policy.

## Data path

```text
Agent / Codex
    -> POST /v1/responses
    -> explicit identity resolution
    -> Responses protocol adapter
    -> lifecycle-aware compiler
    -> upstream POST /responses
    -> byte-preserving JSON or SSE relay
```

Unknown top-level request fields, tools, model settings, and provider-specific options are
preserved. Explicit `input` messages may be budget-selected. Opaque reasoning, function-call,
function-output, computer-use, and other non-message items are treated as mandatory atomic units:
CtxTTL retains and restores them without interpreting or partially removing them.

The adapter keeps `instructions` in its native field while including its cost as mandatory input
during compilation. Rendered lifecycle context is added as explicit system input. Internal adapter
markers never leave the process.

## Explicit-state requirement

CtxTTL can only remove or replace state it can observe. In compile mode, requests containing any
of these server-side references fail closed with HTTP 400:

- `previous_response_id`;
- `conversation`; or
- a hosted `prompt` reference.

Those fields can cause the upstream provider to prepend history or prompt content that the proxy
cannot inspect. Silently accepting them would make lifecycle enforcement unverifiable. Send a
stateless request with complete explicit `input` instead. When
`CTXTTL_MISSING_SESSION_BEHAVIOR=passthrough`, a request without a CtxTTL session identity is relayed
unchanged and explicitly returns `X-CtxTTL-Mode: passthrough`.

## Identity and response headers

The same headers used by the rest of Core apply:

```text
X-CtxTTL-Session-ID: required in compile mode
X-CtxTTL-User-ID: optional
X-CtxTTL-Task-ID: optional
X-CtxTTL-Agent-ID: optional Agent-private scope
X-CtxTTL-Project-ID: optional project sharing and task namespace
X-CtxTTL-Turn-ID: required for turn scope and turn leases
X-CtxTTL-Request-ID: optional idempotency/correlation key
```

Compiled responses expose `X-CtxTTL-Trace-ID`, `X-CtxTTL-Request-ID`,
`X-CtxTTL-Mode: compile`, and compilation/upstream timing headers. Upstream response bytes, status,
content type, request ID, rate-limit headers, and SSE events are relayed without schema rewriting.

On a completely consumed successful response, the execution trace records provider-reported input,
cached-input, and output tokens when present. It also records compilation time, upstream response
start, stream duration, total proxy time, status, outcome, and protocol. Raw prompts and response
content are not written into execution telemetry.

For conversation memory, completed assistant messages are captured from the terminal response
snapshot when available. If a provider omits output from that snapshot, the adapter falls back to
the protocol's completed output-item events. Tool-call and tool-output items remain atomic and are
preserved as explicit input on the next Agent request.

## Codex configuration

Codex custom model-provider settings belong in the user-level `~/.codex/config.toml`, not a
project-local `.codex/config.toml`. A minimal local configuration is:

```toml
model = "your-openai-compatible-model"
model_provider = "ctxttl"

[model_providers.ctxttl]
name = "CtxTTL local gateway"
base_url = "http://127.0.0.1:8765/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
env_http_headers = {
  "X-CtxTTL-Session-ID" = "CTXTTL_SESSION_ID",
  "X-CtxTTL-Agent-ID" = "CTXTTL_AGENT_ID",
  "X-CtxTTL-Project-ID" = "CTXTTL_PROJECT_ID",
  "X-CtxTTL-Task-ID" = "CTXTTL_TASK_ID"
}
```

If Core supplies `CTXTTL_UPSTREAM_API_KEY`, `env_key` may be omitted. Otherwise Codex sends the key
to the local gateway and Core forwards it to the upstream provider. Set stable opaque identities
before starting Codex:

```powershell
$env:CTXTTL_SESSION_ID = "codex-session-001"
$env:CTXTTL_AGENT_ID = "codex-worker"
$env:CTXTTL_PROJECT_ID = "repository-001"
$env:CTXTTL_TASK_ID = "repository-task-001"
codex
```

Do not use user data, repository secrets, or prompt text as identity values. Verify the first
request against the trace before relying on a new Codex release: a client that switches to hidden
`previous_response_id` history will receive the intentional fail-closed error.

## Security and compatibility boundary

The default Core listener is loopback-only. Identity headers define isolation coordinates, not
authentication. Internet or multi-tenant deployment still requires TLS, authentication,
authorization, rate limiting, tenant-specific credentials, storage isolation, and retention
controls in front of Core.

The first Responses milestone supports `POST /responses` over buffered HTTP and SSE. Retrieval,
cancel, delete, input-items listing, WebSocket mode, background-result polling, and provider-hosted
conversation management are not proxied yet.

`GET /v1/models` is a read-only compatibility relay used by Codex capability discovery. Query
parameters, provider authorization, response status, content type, request ID, and body are passed
through without invoking identity resolution, lifecycle compilation, archiving, or tracing.

## Protocol references

- [OpenAI Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [OpenAI streaming Responses guide](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Codex configuration reference](https://developers.openai.com/zh-Hans/docs/config-file/config-reference)
