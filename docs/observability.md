# Observability and compilation traces

Every successful request in `compile` mode creates a `CompilationTrace`. The trace is stored through
the independent `CompilationTraceStore` contract; SQLite implements this contract alongside, but
separately from, context state persistence.

## Recorded metadata

Each trace contains:

- session, optional user/task/turn identity, request ID, model, and streaming mode;
- a SHA-256 request fingerprint rather than the raw request by default;
- source and compiled message counts;
- estimated source and compiled input tokens;
- a balanced token ledger covering lifecycle removal, message budget removal, inactive/redundant
  context, context budget removal, and provider-bound tokens;
- reserved output tokens and effective input limits;
- recent-turn reserve and compiler revision for compatible replay;
- context-state load, history retrieval, pure compiler, and total compilation latency;
- selected context IDs;
- retrieved candidate count, selected retrieved IDs, and raw SQLite BM25 ranks;
- every hard/soft inclusion and exclusion decision with reason, cost, and utility.

The proxy returns the generated ID as `X-CtxTTL-Trace-ID`. Trace contents are intended for developer
debugging and are never injected into the model conversation.

## Query and replay API

- `GET /v1/traces?limit=100` lists traces from the caller's session.
- `GET /v1/traces/{trace_id}` returns a trace only inside that session boundary.
- `GET /v1/traces/{trace_id}/execution` returns the linked provider execution timing.
- `POST /v1/traces/{trace_id}/replay` reruns the pure compiler without calling the provider.

The independent `ExecutionTraceStore` persists the provider-facing outcome as `completed`,
`interrupted`, or `upstream_error`. Buffered calls record the complete transport call duration.
Streaming calls separately record time to upstream response headers and stream-consumption time;
the latter includes provider generation, network transfer, and downstream backpressure. Both forms
record total proxy duration. Successful complete captures also normalize provider-reported input,
cached-input, and output token counters for both Chat Completions and Responses, when the provider
returns them. The execution record identifies its API protocol. These measurements are wall-clock
observations and are not injected into prompts.

For streaming calls, successful telemetry is committed when the protocol terminal marker arrives
(`data: [DONE]` for Chat Completions or a terminal `response.*` event for Responses), before that
terminal block is yielded downstream. This preserves execution evidence when clients such as Codex
close the HTTP stream immediately after receiving completion rather than waiting for transport EOF.

Replay reports whether selected context IDs, token estimates, message count, and every selection
decision match the recorded trace. It also returns the recomputed provider messages. Exact replay
requires both content snapshots and the same supported compiler revision; otherwise the endpoint
returns `409 replay_unavailable` instead of presenting an approximate result as exact.

## Privacy mode

`CTXTTL_TRACE_CAPTURE_CONTENT=false` is the default. In this mode, traces contain metadata and a
request fingerprint but no raw messages or context values. Set it to `true` only when full local
replay is required and the database retention policy is appropriate for the data:

```text
CTXTTL_TRACE_CAPTURE_CONTENT=true
```

Full mode stores both the original message snapshot and all candidate Context IR snapshots. These
may contain personal data, tool output, or secrets and must be protected like the main event store.
Metadata-only traces remain queryable but are intentionally not exactly replayable.

## Storage guarantees

Trace IDs are idempotent. Writing an identical trace again is a no-op; reusing an ID for different
content raises `TraceConflict`. Queries require an explicit session boundary and impose a bounded
limit. Schema version 7 adds execution traces and migrates older databases without rebuilding
context state.

Failed validation or compilation does not currently create a trace because no valid compilation
result exists. A provider failure after successful compilation does create an `upstream_error`
execution trace linked to that compilation.
