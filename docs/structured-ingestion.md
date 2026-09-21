# Structured context ingestion

`POST /v1/context/items` is the explicit path for durable facts, decisions, constraints, and task
state. It does not call an LLM extractor and does not accept raw conversation messages as trusted
state.

The endpoint uses the same identity headers as the transparent data plane. `turn`, `task`,
`agent`, `project`, and `user` scopes require `X-CtxTTL-Turn-ID`, `X-CtxTTL-Task-ID`,
`X-CtxTTL-Agent-ID`, `X-CtxTTL-Project-ID`, and `X-CtxTTL-User-ID`, respectively. The server fixes authority to
`explicit_user`, so clients cannot claim system, developer, or verified-tool authority. Retention
is `persistent` unless an aware UTC-compatible `expires_at` timestamp or positive `ttl_turns`
count is supplied, which creates a `leased` item. The two lease policies are mutually exclusive.

`applicability` is independent from scope and retention:

| Value | Compilation behavior |
| --- | --- |
| `current_turn` | Required for the originating turn and unavailable to later turns. Requires `turn` scope; ingestion assigns `ephemeral` retention. |
| `selective` | Eligible for later requests while active; admitted by relevance and budget. This is the default outside turn scope. |
| `required` | Required on every reachable request while active and cannot be dropped at the target budget. |

This distinction prevents storage duration from being mistaken for permission to inject a record
into every request. Untrusted retrieved content, summaries, and assistant inferences cannot declare
themselves `required`.

```http
POST /v1/context/items
X-CtxTTL-Session-ID: demo-session
X-CtxTTL-Task-ID: demo-task
Content-Type: application/json

{
  "kind": "decision",
  "scope": "task",
  "applicability": "required",
  "subject": "project.database",
  "value": "PostgreSQL",
  "reason": "production deployment requirement"
}
```

To correct an active assertion, send the replacement with `supersedes` set to the prior item ID.
The event and active-state projection change atomically. Reusing an active owner/scope/subject
without an explicit correction returns `409 context_conflict`; cross-boundary correction is also
rejected.

Clients may send `X-CtxTTL-Request-ID` as an idempotency key. The mutation and its request record
commit in the same transaction. Retrying the same semantic request returns the original event and
item with `applied: false`; reusing that key in the same session with changed input or ownership
coordinates returns `409 request_conflict`. When omitted, the server generates a request ID and
returns it in the response header.

Supported kinds are `fact`, `decision`, `constraint`, and `task_state`. Supported scopes are
`turn`, `session`, `task`, `agent`, `project`, and `user`.

## Query and lifecycle API

- `GET /v1/context/items?status=active&limit=100` lists only identity-reachable items.
- `GET /v1/context/items/{context_id}` returns one reachable item.
- `POST /v1/context/items/{context_id}/retract` records an explicit withdrawal.
- `POST /v1/context/items/{context_id}/expire` explicitly ends a lease or assertion.

Lifecycle POST bodies accept optional `reason` and `turn_id` fields. Unreachable IDs return 404,
which avoids revealing whether another ownership boundary contains the item.

Time-based leases are checked lazily before active state is listed, fetched, or compiled. A due
item receives a real `expire` event and is removed from the active projection atomically. This
does not require a background scheduler.

Turn-count leases require a turn ID. A lease with `ttl_turns: N` is available for the first `N`
distinct compiled turns observed after it is asserted and expires before compilation on the next
distinct turn. A retry with the same session and turn ID is counted once. For safety, a
turn-count lease is not compiled when a chat request omits the turn ID.
