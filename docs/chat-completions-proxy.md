# Chat Completions Proxy Contract

M1 established the OpenAI-compatible transport endpoint, and M3 adds context compilation behind
the same contract:

```text
POST /v1/chat/completions
```

It supports buffered JSON responses and byte-preserving SSE relay when `stream` is `true`.

## Request behavior

- The body must be a JSON object.
- `stream`, when present, must be a boolean.
- Other top-level JSON fields are forwarded without local schema narrowing.
- A valid session header is required unless passthrough behavior is configured.
- `X-CtxTTL-Request-ID` is an optional client idempotency key; the proxy generates one otherwise.
- `X-CtxTTL-Turn-ID` enables turn-scoped context and retry-safe `ttl_turns` accounting.
- `X-CtxTTL-Agent-ID` enables Agent-private state; `X-CtxTTL-Project-ID` enables project sharing
  and namespaces Agent/task state.
- CtxTTL identity headers are never forwarded to the upstream provider.
- The configured upstream API key overrides client Authorization.
- When no upstream API key is configured, client Authorization is forwarded.
- Organization, project, and idempotency headers are forwarded; cookies and arbitrary headers are not.

`X-CtxTTL-Mode: compile` means the request passed an explicit identity boundary and the M3
compiler processed its messages before upstream dispatch. `X-CtxTTL-Mode: passthrough` means the
payload was relayed unchanged. Compilation does not automatically extract or persist new facts
from the request; it consumes active structured state already in the event store. See
`context-compiler.md` for selection and protocol-repair semantics.

## Response behavior

- Upstream status codes and response bodies are preserved.
- Content type, request ID, retry, OpenAI, and rate-limit headers are preserved.
- Cookies and hop-by-hop headers are not relayed.
- Upstream connection failures become an OpenAI-shaped HTTP 502 error.
- Invalid local requests become an OpenAI-shaped HTTP 400 error.
- Streaming resources are closed after completion, interruption, or client cancellation.
- Responses expose `X-CtxTTL-Request-ID` and, in compile mode, `X-CtxTTL-Trace-ID`.
- Complete buffered and fully consumed SSE assistant output is archived on a best-effort basis.

## Compatibility boundary

CtxTTL validates the fields required to route and compile safely. Any Agent platform that can set
the standard identity headers uses this route directly; no platform adapter is required. It preserves unknown fields in
retained messages and leaves full Chat Completions schema validation to the upstream provider.
