# CtxTTL Identity Protocol

[简体中文](zh-CN/identity-protocol.md)

CtxTTL must know the ownership boundary of a request before it stores or retrieves context. It
does not infer identity from message text.

## Headers

| Header | Required | Meaning |
|---|---:|---|
| `X-CtxTTL-Session-ID` | Yes | Conversation/session isolation boundary |
| `X-CtxTTL-User-ID` | No | Cross-session user scope |
| `X-CtxTTL-Task-ID` | No | Task scope within or across sessions |
| `X-CtxTTL-Agent-ID` | No | Private scope for one Agent role or worker |
| `X-CtxTTL-Project-ID` | No | Shared project scope and task namespace |
| `X-CtxTTL-Turn-ID` | No | Logical-turn scope and turn-lease accounting |
| `X-CtxTTL-Request-ID` | No | Idempotency and request correlation |

Header names are configurable. Header values are opaque identifiers containing 1-128 ASCII
letters, digits, dots, underscores, colons, or hyphens.

If both `X-CtxTTL-User-ID` and the OpenAI-compatible request `user` field exist, the explicit
header wins. The `user` field is never used as a session identifier.

Turn IDs are explicit logical identifiers, not message-array positions. Reusing the same turn ID
for a retried request does not consume another turn from a turn-count lease.

Agent and project coordinates are protocol concepts, not framework types. `agent` scope isolates
state for one Agent across its sessions in the supplied user/project namespace. `task` and
`project` scopes intentionally allow collaboration by different Agent IDs that present the same
coordinates. Identity values select ownership boundaries; they do not authenticate callers.

## Missing session behavior

The default is `reject`, which will become an HTTP 400 response in the proxy milestone. Operators
may configure `CTXTTL_MISSING_SESSION_BEHAVIOR=passthrough`; in that mode the request is sent to the
upstream provider without context compilation or memory writes.

This explicit behavior prevents accidental cross-session memory contamination.
