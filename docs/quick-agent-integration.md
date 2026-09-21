# Connect an existing Agent

[简体中文](zh-CN/quick-agent-integration.md)

CtxTTL sits between an Agent and its model provider. You do not replace the Agent loop, planner,
tools, or model SDK, and you do not need a framework-specific CtxTTL adapter.

## Choose one data path

| Existing Agent capability | CtxTTL path | Change required |
| --- | --- | --- |
| Configurable OpenAI-compatible base URL and request headers | Transparent proxy | Change base URL and add identity headers |
| Hook immediately before a model request | Compile-only | POST the provider payload to `/v1/context/compile` |
| Neither of the above | External gateway/plugin hook | Add one standards-based interception point to the Agent |

MCP is an optional control plane for all three paths. It lets an Agent explicitly assert,
supersede, retract, expire, inspect, and compile state, but it does not intercept model calls by
itself.

## Path A: transparent OpenAI-compatible proxy

Start Core with an upstream provider configured in `.env`:

```dotenv
CTXTTL_UPSTREAM_BASE_URL=https://your-provider.example/v1
CTXTTL_UPSTREAM_API_KEY=replace-me
```

```powershell
.\.venv\Scripts\python.exe -m ctxttl
```

On macOS/Linux, activate the virtual environment and run `python -m ctxttl`.

Then change the Agent's model base URL to:

```text
http://127.0.0.1:8765/v1
```

Send the ordinary model request with stable identity headers:

```text
X-CtxTTL-Session-ID: run-42
X-CtxTTL-Agent-ID: researcher
X-CtxTTL-Project-ID: project-alpha
X-CtxTTL-Task-ID: investigate-regression
X-CtxTTL-Turn-ID: turn-3
```

Use `POST /v1/responses` for Responses clients and `POST /v1/chat/completions` for Chat
Completions clients. JSON and SSE stay on their native wire protocol. CtxTTL identity headers are
consumed locally and are not forwarded to the provider.

### Python/OpenAI-compatible clients

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="local-gateway-key",
    default_headers={
        "X-CtxTTL-Session-ID": "run-42",
        "X-CtxTTL-Agent-ID": "researcher",
        "X-CtxTTL-Project-ID": "project-alpha",
        "X-CtxTTL-Task-ID": "investigate-regression",
    },
)
```

The same configuration pattern applies to JavaScript clients and Agent frameworks that expose
OpenAI-compatible provider settings.

## Path B: compile-only hook

If the Agent owns its provider client, call CtxTTL immediately before that client:

```python
import httpx

identity = {
    "X-CtxTTL-Session-ID": "run-42",
    "X-CtxTTL-Agent-ID": "researcher",
    "X-CtxTTL-Project-ID": "project-alpha",
    "X-CtxTTL-Task-ID": "investigate-regression",
}
provider_payload = {
    "model": "your-model",
    "messages": [{"role": "user", "content": "Continue the investigation"}],
}

compiled = (
    httpx.post(
        "http://127.0.0.1:8765/v1/context/compile",
        headers=identity,
        json=provider_payload,
    )
    .raise_for_status()
    .json()
)

# Send compiled["payload"] through the Agent's existing provider client.
print(compiled["trace_id"], compiled["metrics"])
```

Do not send the original payload after compilation. Doing so would bypass lifecycle enforcement.
The compile-only route does not call a model.

## Optional MCP control plane

An MCP-capable Agent can install `packages/ctxttl-mcp` and use seven explicit lifecycle tools.
Give the MCP tools the same `session_id`, `agent_id`, `project_id`, and `task_id` values used on
the model request. This makes state written over MCP immediately reachable through the transparent
or compile-only data path.

See [MCP integration](mcp-integration.md) for installation and security settings.

## Which scope should the Agent write?

| Desired lifetime or visibility | Scope |
| --- | --- |
| Only this turn | `turn` |
| Only this conversation/run | `session` |
| Private to one Agent role across sessions | `agent` |
| Shared by Agents doing one task | `task` |
| Shared across a project | `project` |
| Shared across a user's sessions | `user` |

Use lifecycle operations when the truth changes: `supersede` a replaced decision, `retract` a
withdrawn assertion, and `expire` temporary state. CtxTTL cannot safely infer these business events
from arbitrary prompt prose.

## Acceptance check

Before production use, verify one request end to end:

1. The response has `X-CtxTTL-Mode: compile`, `X-CtxTTL-Request-ID`, and
   `X-CtxTTL-Trace-ID`.
2. The trace is reachable only with the expected identity coordinates.
3. Agent-private state is absent for another Agent ID.
4. Task/project state is visible to a second authorized Agent using the same shared coordinates.
5. A superseded or expired test item does not appear in the compiled payload.
6. Streaming and tool-call groups remain valid for the Agent's exact model protocol.

Identity headers are routing and isolation coordinates, not authentication. An Internet-facing or
multi-tenant deployment must put TLS, authentication, authorization, rate limits, and trusted
header injection in front of Core.
