# Context state storage

M2 adds a provider-neutral persistence boundary for context lifecycle state. The application
layer depends on the `ContextStateStore` protocol; SQLite is one replaceable adapter behind that
contract.

## Data model

The SQLite schema stores three related views:

- `events` is the immutable lifecycle log. It records `assert`, `supersede`, `retract`, and
  `expire` operations.
- `context_items` stores the assertion created by an event, including terminal status changes.
- `state_assertions` is the small active-state projection used by future context compilation.
- `turn_observations` and `context_turn_leases` provide retry-safe logical-turn accounting.
- `mutation_requests` binds structured-write idempotency keys to their original results.

The complete Pydantic object is retained as JSON for lossless reconstruction. Selected fields are
also stored in typed columns for ownership filtering, ordering, constraints, and later migrations.

## Ownership and isolation

Every item and event has an explicit `ContextOwner`. A deterministic owner key is derived from the
item scope:

| Scope | Isolation key |
| --- | --- |
| turn | session + turn |
| session | session |
| task | user (or originating session) + task |
| project | user (or originating session) + project |
| user | user |

Lifecycle transitions must remain within the same owner key, scope, and subject. This allows a
later session for the same user and task to correct an earlier assertion without exposing it to a
different user.

## Transaction and correction semantics

Each operation runs under `BEGIN IMMEDIATE`. Event insertion, context-item mutation, and active
projection mutation commit together or roll back together. A subject can have only one active
assertion inside an ownership boundary. Replacing it therefore requires an explicit `supersede`
event; a second plain assertion is rejected.

Retraction and expiry keep the historical item and event while removing it from the active
projection. Supersession marks the old item terminal and inserts the replacement atomically.

The application layer checks `expires_at` before active state is returned to management APIs or
the compiler. Due items receive an explicit system-authority `expire` event. This is lazy expiry:
an idle database is not mutated until the relevant ownership boundary is accessed.

Turn observations are unique by owner boundary, originating session, and explicit turn ID. A
turn-count lease is compiled for exactly its configured number of distinct observed turns and
receives a system-authority `expire` event before the following turn is compiled. Recording a
turn and incrementing its matching lease counters run in one SQLite transaction.

## Idempotency

Event IDs are idempotency keys. Replaying the exact same event returns the already persisted
result with `applied=False`, even if that item has since reached a terminal state. Reusing an event
ID with different event or item content raises `EventConflict`.

The structured ingestion API adds a higher-level request ledger. Its session, request ID,
operation, semantic fingerprint, and original result are persisted in the same transaction as the
lifecycle event. This closes the response-loss window that event IDs alone cannot cover because
event IDs are generated inside the service.

## Runtime use

The adapter is initialized with a standard SQLite URL:

```python
from ctxttl.storage import SQLiteContextStateStore

store = SQLiteContextStateStore("sqlite:///./data/ctxttl.db")
await store.initialize()
```

Call `await store.aclose()` during application shutdown. The storage boundary is used by explicit
ingestion, lifecycle management, automatic time expiry, and request-time context compilation.
