# Universal Agent integration protocol

[简体中文](zh-CN/agent-integrations.md)

CtxTTL integrates at the model-context boundary, not inside an Agent loop. Any runtime that can
make HTTP requests or configure an OpenAI-compatible base URL uses the same protocol; CtxTTL does
not require a framework-specific adapter.

```text
any Agent runtime
    -> standard CtxTTL identity headers
    -> /v1/responses | /v1/chat/completions | /v1/context/compile
    -> one lifecycle compiler and state model
    -> upstream model or compiled payload
```

The wire contract is deliberately smaller than a framework SDK. Planning, tool execution, model
selection, and framework objects remain outside Core. This keeps integrations replaceable and
prevents a LangGraph, OpenClaw, Codex, or custom-runtime concept from entering lifecycle policy.

## One identity contract

Every data-plane and control-plane operation uses these opaque coordinates:

| Header / MCP argument | Required | Ownership boundary |
| --- | ---: | --- |
| `X-CtxTTL-Session-ID` / `session_id` | Yes | One conversation or execution session |
| `X-CtxTTL-Agent-ID` / `agent_id` | No | Private state for one Agent role or worker |
| `X-CtxTTL-Project-ID` / `project_id` | No | State shared across a project |
| `X-CtxTTL-Task-ID` / `task_id` | No | State shared by Agents working on one task |
| `X-CtxTTL-User-ID` / `user_id` | No | State shared across that user's sessions |
| `X-CtxTTL-Turn-ID` / `turn_id` | No | Current-turn state and retry-safe turn leases |
| `X-CtxTTL-Request-ID` | No | Idempotency and correlation |

The caller chooses stable, non-secret identifiers. CtxTTL never infers ownership from prompt text.
Two Agents given the same project and task coordinates can reach shared task/project state while
their `agent`-scoped and `session`-scoped state remains isolated. Omitting Agent and Project IDs
retains the pre-existing session/task/user behavior.

The same coordinates are supported by the optional MCP control plane, so a runtime can explicitly
assert or expire state over MCP and have the transparent HTTP data plane compile that state without
translation tables.

## Three universal entry points

| Need | Endpoint | Model call |
| --- | --- | ---: |
| Responses-compatible transparent gateway | `POST /v1/responses` | Yes |
| Chat Completions-compatible transparent gateway | `POST /v1/chat/completions` | Yes |
| Compile-only integration or custom provider client | `POST /v1/context/compile` | No |

The body sent to the first two routes is the normal provider request. The compile-only route
returns `payload`, `trace_id`, `request_id`, and token/selection metrics; the caller sends the
returned payload to its provider. All three routes use the same identity resolution, lifecycle
projection, compiler, storage, and trace schema.

Minimal Chat Completions example:

```http
POST /v1/chat/completions
X-CtxTTL-Session-ID: run-42
X-CtxTTL-Agent-ID: researcher
X-CtxTTL-Project-ID: project-alpha
X-CtxTTL-Task-ID: investigate-regression
X-CtxTTL-Turn-ID: turn-3
Content-Type: application/json

{"model":"your-model","messages":[{"role":"user","content":"Continue the investigation"}]}
```

Switching the runtime does not change the endpoint, body schema, or CtxTTL semantics. A platform
only needs to expose these values as request headers. When that is impossible, call the compile-only
endpoint immediately before the platform's existing model client.

## Multi-Agent sharing model

Choose scope according to who should see the state:

| Scope | Intended visibility |
| --- | --- |
| `turn` | One logical turn |
| `session` | One conversation/execution session |
| `agent` | One Agent across its sessions in the supplied user/project namespace |
| `task` | All Agents with the same task in the supplied user/project namespace |
| `project` | All Agents in the supplied user/project namespace |
| `user` | That user across projects and sessions |

The coordinates are capability selectors, not authentication. A public or multi-tenant deployment
must authenticate the caller and authorize which IDs it may claim before the request reaches Core.
Use gateway-issued headers rather than trusting arbitrary Internet clients.

## Integration acceptance contract

An Agent platform is compatible when it can pass the following black-box checks without adding
framework code to Core:

1. The same session compiles deterministically and emits a reachable trace.
2. Two different Agent IDs cannot read each other's `agent`-scoped state.
3. Those Agents can both read state intentionally stored at their common task or project scope.
4. Superseded, retracted, and expired state is absent from both transparent and compile-only paths.
5. Tool-call groups remain atomic, streaming remains valid, and upstream provider fields survive.
6. Missing explicit state or an impossible required-context budget fails closed.
7. Real provider telemetry records input, cached-input, output, latency, status, retries, and outcome
   when the provider reports them.

Passing these checks establishes protocol compatibility and isolation. It does not by itself prove
token, latency, cost, or answer-quality improvements; those require a separately designed and
reviewed evaluation.

## Python application port

In-process Python systems may depend on `AgentContextPort` instead of HTTP. `AgentContextRequest`
uses the same session, Agent, project, task, user, turn, and request coordinates.
`LifecycleAwareAgentContext` is the default implementation. This is an application port, not a
framework adapter; no compiler or persistence policy should be copied into the caller.

## Legacy OpenClaw compatibility

The existing `/v1/integrations/openclaw/*` routes remain for backward compatibility. They hash
OpenClaw's historical coordinate names and translate them into the universal contract before
calling the same application services. New integrations should use the generic endpoints above.
The compatibility route is not the architectural extension point and no equivalent route is
needed for each new Agent framework.

OpenClaw-specific inactive-turn markers remain supported only on that legacy route. A generic
runtime should represent durable lifecycle changes through `/v1/context/items` or MCP instead of
embedding platform-specific control syntax in prompts.
