# Known limitations

[简体中文](zh-CN/known-limitations.md)

CtxTTL 0.2.1 is a research MVP. Its current boundaries are
intentional and should remain visible:

- Chat Completions, the explicit-stateless subset of Responses `POST /responses`, and read-only
  model-catalog discovery through `GET /models` are proxied.
  Responses requests using `previous_response_id`, hosted conversations, or hosted prompts fail
  closed in compile mode because their hidden upstream state cannot be lifecycle-filtered. Other
  Responses resource endpoints and WebSocket transport are not implemented.
- The generic protocol does not own any Agent loop. The OpenClaw-specific routes are legacy
  compatibility translations. The optional MCP package remains a lifecycle control plane; only
  the model proxy enforces compilation on every request routed through it.
- Identity headers define isolation coordinates but are not authentication. Do not expose the
  service to untrusted tenants without an authentication and authorization layer.
- Multi-Agent scope isolation and sharing are contract-tested locally, but broad compatibility
  claims still require black-box runs against multiple independent Agent runtimes.
- Output capture supports common buffered and SSE Chat Completions shapes and terminal Responses
  events. Oversized, malformed, failed, or cancelled streams are not treated as complete output.
- SQLite FTS5 retrieval is lexical. There is no vector search, learned reranker, or semantic
  extraction model in v0.2.
- Authority labels and evidence-only wrappers reduce accidental instruction escalation, but no
  prompt-only boundary can guarantee immunity to prompt injection from hostile retrieved text.
- Structured facts, decisions, constraints, and task state require explicit API ingestion. CtxTTL
  does not claim reliable automatic LLM extraction.
- The token estimator is deterministic and dependency-free, but approximate. Provider-native
  tokenizers can be introduced behind the estimator port.
- SQLite is suitable for local MVP evaluation, not a claim of distributed or high-concurrency
  production readiness.
- Raw archive retention defaults to 30 days. Operators remain responsible for secrets, personal
  data, export, deletion, backups, and applicable policy requirements.
- Exact compiler replay requires content capture at the time of the request and a supported
  compiler revision; metadata-only traces remain inspectable but cannot reconstruct private input.
- Built-in fixtures validate replay mechanics and lifecycle behavior. They do not establish answer
  accuracy, market value, or superiority over other memory systems.
- Integration acceptance does not establish token, latency, cost, or model-quality improvements.
  Comparative claims require an independently reviewed evaluation outside this repository.
