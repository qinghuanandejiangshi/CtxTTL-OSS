# Deterministic benchmark fixture

The bundled `benchmarks/ctxttlbench.jsonl` fixture verifies public compiler contracts without a
model or external credentials. It covers lifecycle projection, scope filtering, budget behavior,
strategy wiring, and replay metadata. It is an engineering regression fixture, not a model-quality
benchmark or comparative research result.

Run it on Windows:

```powershell
.\.venv\Scripts\python.exe -m ctxttl.benchmarks.cli benchmarks\ctxttlbench.jsonl
```

Run it on macOS or Linux:

```bash
python -m ctxttl.benchmarks.cli benchmarks/ctxttlbench.jsonl
```

Generated reports belong in ignored local output directories. Third-party datasets, private task
traces, provider responses, frozen selections, and unpublished protocols must not be committed to
this repository.
