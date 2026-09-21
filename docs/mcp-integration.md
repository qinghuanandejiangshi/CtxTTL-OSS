# MCP control-plane integration

[简体中文](zh-CN/mcp-integration.md)

`packages/ctxttl-mcp` exposes CtxTTL lifecycle operations to Codex and other MCP hosts over
Streamable HTTP. It is an independently packaged control plane:

```text
MCP host -> ctxttl-mcp -> public CtxTTL HTTP contract -> CtxTTL Core
```

Core has no dependency on the MCP SDK. The adapter can be upgraded, restarted, or replaced without
changing the compiler, storage, Chat Completions proxy, or Agent port.

## Start locally

Start Core first:

```powershell
.\.venv\Scripts\python.exe -m ctxttl
```

Install and start the adapter:

```powershell
.\.venv\Scripts\python.exe -m pip install -e packages\ctxttl-mcp
$env:CTXTTL_MCP_CORE_URL = "http://127.0.0.1:8765"
$env:CTXTTL_MCP_BEARER_TOKEN = "replace-with-a-long-random-token"
.\.venv\Scripts\ctxttl-mcp.exe
```

Register the endpoint with Codex:

```powershell
$env:CTXTTL_MCP_TOKEN = "replace-with-the-same-token"
codex mcp add ctxttl --url http://127.0.0.1:8766/mcp --bearer-token-env-var CTXTTL_MCP_TOKEN
codex mcp list
```

Keep the token in an environment variable, not in repository configuration.

## Tool contract

| Tool | Purpose |
| --- | --- |
| `ctxttl_context_assert` | Create a durable or leased fact, decision, constraint, or task state. |
| `ctxttl_context_supersede` | Atomically replace a reachable active item. |
| `ctxttl_context_retract` | Withdraw an item that is no longer true or authorized. |
| `ctxttl_context_expire` | End an item whose intended lifetime has finished. |
| `ctxttl_context_list` | List state reachable from explicit identity coordinates. |
| `ctxttl_compile_request` | Compile a Chat Completions payload without calling a model. |
| `ctxttl_trace_get` | Inspect one reachable compilation trace. |

Every tool requires a stable opaque `session_id` and accepts the same optional `agent_id`,
`project_id`, `task_id`, `user_id`, and `turn_id` coordinates as the HTTP data plane. Do not put
prompts, email addresses, channel names, access tokens, or other sensitive values in identity
fields. Agent, project, task, user, and turn scopes require their corresponding coordinates.

## Operational boundary

MCP is the explicit state control plane; it does not guarantee that a host calls a tool before
every inference and it does not replace the host's internal context window. Transparent enforcement
is provided separately by the bounded [Responses API data plane](responses-api-proxy.md).
`ctxttl_compile_request` does not call a model, but it records the compilation trace/archive and
observes turn-scoped lifecycle state. It is not a read-only preview.

The adapter logs structured diagnostic events without raw identity or context values. Its default
listener is `127.0.0.1:8766`; a non-loopback bind is rejected unless a bearer token is configured.
Static bearer authentication is suitable for a controlled local pilot; internet or multi-tenant
deployment additionally requires TLS, OAuth or gateway-issued credentials, authorization, rate
limits, and tenant isolation.
