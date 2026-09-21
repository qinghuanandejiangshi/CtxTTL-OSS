# Contributing

CtxTTL is in an early research and MVP phase. Changes should preserve clear ownership boundaries
and produce evidence that can later be reproduced in the paper.

## Local checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_repository_hygiene.py
```

Use `python -m ruff format .` to format code. Do not weaken checks to make a change pass.

## Pull requests and commits

- Keep changes narrowly scoped and use Conventional Commit messages.
- Explain the observable behavior, tests, and architectural impact.
- Do not mix unrelated formatting or generated files into a feature commit.
- Add or update documentation when a public contract changes.
- Report benchmark model, parameters, dataset revision, cost accounting, and raw result location.
- Never claim performance from an unrepeatable or selectively reported run.
- Follow the tracked/generated/evidence boundaries in
  [`docs/repository-policy.md`](docs/repository-policy.md).

## Tests

- Unit tests cover domain invariants and pure policies.
- Contract tests are shared by interchangeable adapters.
- Integration tests cover HTTP, SQLite, streaming, cancellation, and error propagation.
- Benchmark tests are not substitutes for correctness tests.
- Tests must not require production credentials unless explicitly marked and excluded from CI.

## Architecture decisions

Material changes to Context IR, lifecycle semantics, identity boundaries, storage contracts, or
compilation ordering require a short architecture decision record. Record the context, decision,
alternatives, and consequences; do not rewrite accepted history after implementation.
