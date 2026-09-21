# Architecture

[简体中文](docs/zh-CN/architecture.md)

CtxTTL is organized as replaceable layers. Dependencies point inward toward stable domain
contracts; domain code never imports HTTP, database, framework, or provider implementations.

```text
API / CLI
    ↓
Application services
    ↓
Domain models and ports
    ↑
Infrastructure adapters
```

## Module boundaries

| Area | Responsibility | Must not own |
|---|---|---|
| `models` | Provider-neutral Context IR and lifecycle events | HTTP, SQL, provider payloads |
| `identity` | Resolve explicit ownership boundaries | Authentication, persistence |
| `config` | Parse and validate runtime configuration | Business decisions |
| `api` | HTTP protocol translation and dependency wiring | Context selection policy |
| `application` | Coordinate use cases through interfaces | Framework-specific transport details |
| `archive` | Raw conversation archive models and retrieval port | Compilation policy, SQL |
| `compiler` | Pure context compilation policies | Database and network I/O |
| `storage` | Persistence ports and replaceable adapters | Compilation policy |
| `providers` | Upstream LLM transport adapters | Persistent state and retrieval policy |
| `observability` | Structured traces and metrics | Business decisions |
| `benchmarks` | Baselines and reproducible evaluation | Production request handling |
| `evaluation` | Model and judge ports, checkpoints, ledgers, and paired metrics | Production request handling, compiler policy |
| `integrations` | Legacy compatibility adapters over universal identity contracts | Agent loop, provider selection, lifecycle policy |
| `packages/ctxttl-mcp` | Optional MCP control-plane adapter over the public Core HTTP contract | Compiler, storage, provider calls, Agent loop |

Modules listed for future milestones are created only when their first concrete capability is
implemented. Empty architecture folders are avoided.

## Dependency rules

1. Domain models may depend on Pydantic and the Python standard library only.
2. Compiler policies receive data through parameters and return explicit results; they do not use
   global settings, network clients, or database connections.
3. Storage and provider implementations satisfy narrow protocols owned by the consuming layer.
4. FastAPI objects remain inside the API layer.
5. Provider-specific fields remain lossless at protocol boundaries unless a documented compiler
   rule transforms them.
6. Public behavior is tested through contracts; adapter tests may be reused by every implementation.
7. Cross-module imports must follow the dependency direction above. Circular imports are defects.
8. Agent runtimes use the universal HTTP or application identity contract directly. Legacy
   integrations may translate external coordinates, but they never duplicate compiler or
   persistence behavior and are not required for each new framework.
9. Benchmark utilities may depend on the production package, but production modules never import
   benchmark code.
10. Optional integration packages depend only on public HTTP or application contracts. Core never
    imports their SDKs or implementation modules.
11. Provider protocol adapters may translate explicit request items into the compiler's stable
    message boundary, but they must preserve opaque items atomically and never own lifecycle policy.

## Change discipline

- One commit should represent one coherent capability or refactor.
- Refactoring and behavior changes should be separate when practical.
- Every behavior change requires tests at the lowest useful layer.
- Breaking contracts require an architecture decision record under `docs/decisions/`.
- Secrets, local databases, traces containing private content, and `.env` files are never committed.
