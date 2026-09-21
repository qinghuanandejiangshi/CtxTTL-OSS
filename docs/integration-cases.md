# Integration acceptance cases

[简体中文](zh-CN/integration-cases.md)

These cases document public product-contract acceptance only. They do not publish private
workloads, trajectories, provider payloads, comparative results, or research protocols.

## OpenAI-compatible data plane

An existing Agent can route explicit stateless Chat Completions or Responses requests through
CtxTTL by changing its base URL and attaching stable identity headers. Contract tests cover
buffered and streaming relay, model discovery, lifecycle compilation, trace creation, and
fail-closed handling of hidden upstream state.

## MCP control plane

The separately packaged MCP server exposes explicit lifecycle and trace operations without adding
an MCP dependency to Core. MCP manages state; the HTTP data plane remains responsible for
enforcing compilation on model requests.

## Universal multi-Agent contract

The public HTTP contract test uses two Agent IDs in different sessions. Agent-private context is
isolated while task and project context can be shared intentionally. No framework object or
runtime-specific compiler path participates in the test.

These checks establish wire compatibility and isolation behavior. They do not establish a general
token, latency, cost, or answer-quality advantage. New runtimes should follow the
[quick integration guide](quick-agent-integration.md) and run the same black-box acceptance checks.
