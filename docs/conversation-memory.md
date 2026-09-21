# Conversation memory

CtxTTL archives the raw messages supplied in compiled Chat Completions requests. The archive is a
separate port from active Context IR state: applications can replace SQLite FTS5 without changing
the compiler or provider transport.

## Request flow

1. Resolve explicit session, task, and user ownership.
2. Search only archive entries reachable through those owner keys.
3. Remove hits already present in the current request.
4. Convert remaining hits to `message` Context IR with `archived` retention and
   `retrieved_source` authority.
5. Let the normal token-budget compiler decide whether to include each hit.
6. Persist the source request messages after compilation.
7. Capture complete buffered or fully consumed SSE assistant output without changing response
   bytes, then append it as output occurrences.

Retrieval occurs before archiving the current request, so a question cannot retrieve itself.
History-reference phrases such as `之前`, `上次`, `before`, and `remember` use the larger reference
limit. Retrieval remains lexical and model-free in v0.1; it is evidence, never an automatically
trusted constraint.

SQLite uses an FTS5 trigram index so Chinese substrings and English terms work in the same store.
Every message is keyed by session, request ID, direction, and position. Replaying the same
occurrence with the same content is idempotent; reusing it with different content is a conflict.
Identical text in different requests remains distinct, preserving conversation chronology.

Clients may supply `X-CtxTTL-Request-ID` to make archive writes idempotent across network retries.
When omitted, the proxy creates a fresh request ID and returns it in the response header. Input and
output positions use separate namespaces.

## Privacy and retention

Raw request messages may contain private or tool-supplied content. They are separate from
metadata-only compilation traces. Archiving is enabled by default and can be disabled with
`CTXTTL_ARCHIVE_ENABLED=false`. The default retention window is 30 days. Set
`CTXTTL_ARCHIVE_RETENTION_DAYS` to another positive day count, or leave it empty only when
indefinite retention is intentional. Set
`CTXTTL_HISTORY_RETRIEVAL_ENABLED=false` to preserve the archive without injecting retrieval hits.

Buffered assistant messages and fully consumed SSE assistant text, refusal, legacy function-call,
and tool-call deltas are captured. Capture is bounded by `CTXTTL_RESPONSE_CAPTURE_MAX_BYTES`
(2 MB by default). Oversized, malformed, failed, or client-cancelled streams remain byte-preserved
but are not archived as complete output.
