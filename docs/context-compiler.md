# Context compiler

M3 connects isolated active state to the Chat Completions proxy through a deterministic compiler.
The implementation separates five replaceable responsibilities:

- `compiler/models.py` defines result, decision, error, and token-estimator contracts.
- `compiler/tokenization.py` provides the dependency-free MVP estimator.
- `compiler/relevance.py` provides a deterministic, dependency-free query relevance adapter.
- `compiler/budget.py` implements generic hard/soft candidate selection.
- `compiler/openai.py` validates and groups OpenAI Chat Completions messages, maps context into
  candidates, and renders the selected result.

The application service retrieves state and computes the request-specific input ceiling. The API
and SQLite layers do not participate in selection policy.

## Compilation order

For every request in `compile` mode, CtxTTL performs the following steps:

1. Validate message objects and roles.
2. Group each assistant Tool Call with all of its Tool Result messages.
3. Group ordinary conversation into complete user turns.
4. Load active session, task, and user context visible to the resolved identity.
5. Remove inactive and redundant context candidates.
6. Include mandatory candidates and reject the request if they exceed the safety ceiling.
7. Select soft candidates by deterministic utility until the target budget is reached.
8. Render selected structured context after leading system/developer messages while preserving the
   original order of selected conversation turns.

Unknown top-level provider fields and unknown fields inside retained messages are copied without
schema loss.

## Hard inclusion

Hard inclusion is intentionally narrow:

- all system and developer messages;
- the configured number of most recent complete user turns;
- complete Tool Call/Tool Result groups whenever their containing turn is selected;
- system and developer context;
- explicit-user constraints, decisions, task state, and corrections;
- verified-tool task state.
- context explicitly classified as `current_turn` or trusted context classified as `required`.

Retrieved content, summaries, and assistant inferences cannot become mandatory merely by being
labelled as a constraint. This prevents lower-authority content from bypassing the soft budget and
reduces accidental authority escalation.

Applicability is orthogonal to lifecycle. `current_turn` records are required only within their
turn ownership boundary, `selective` records remain eligible for relevance-based reuse, and
`required` records remain mandatory until their scope becomes unreachable or a lifecycle event
expires, retracts, or supersedes them.

## Soft selection

Soft candidates use an explicit utility score. Context scoring combines priority, confidence,
authority, scope, and relevance to the latest user query. The default relevance adapter measures
normalized lexical coverage with English stop-word filtering and CJK bigrams. It implements the
`ContextRelevanceScorer` port and can be replaced by an embedding model or reranker without changing
budget, lifecycle, API, or storage code. Older conversation turns receive a monotonic recency
score. Equal scores use a typed, stable tie-breaker, so input order does not change the result.

Every candidate produces a decision with its token cost and one of these reasons:

- `hard_required`
- `soft_selected`
- `inactive`
- `redundant`
- `budget_exceeded`

M4 persists these decisions as compilation traces. The response exposes the corresponding trace ID
in `X-CtxTTL-Trace-ID` without exposing internal decision details to the model conversation.

## Token safety

`target_context_tokens` is the normal input target. `max_context_tokens` is a hard total ceiling.
When the request declares `max_completion_tokens` or legacy `max_tokens`, that output allowance is
reserved before input compilation. Mandatory input may exceed the target but never the remaining
maximum; if it does, CtxTTL fails closed instead of silently dropping instructions or tool results.

The MVP estimator deliberately avoids a tokenizer dependency and estimates serialized UTF-8 bytes
conservatively. Exact model tokenizers can replace it through the `TokenEstimator` protocol.

## Current limitations

- M3 retrieves structured state already present in the event store; it does not automatically
  extract facts or decisions from arbitrary conversation text.
- Project-scoped retrieval awaits an explicit project identity protocol.
- Context records are rendered as clearly labelled JSON system messages for broad Chat Completions
  compatibility. Provider-specific renderers can replace this policy later.
- Rendering assigns an explicit `instruction_policy`: only system/developer records are directives;
  explicit-user records are user-stated context; retrieved sources, summaries, and assistant
  inferences are marked as evidence-only. The wrapper tells the model never to execute commands
  embedded in untrusted values. This is defense in depth, not a claim that prompt injection is
  completely solved.
- Exact provider tokenizers remain future adapters; the current estimator is intentionally
  conservative and model-agnostic.
- The default lexical relevance adapter cannot resolve paraphrases with no shared terms. Semantic
  scoring remains an optional replaceable adapter rather than a hidden model dependency.
