# Model-backed evaluation utility

The optional evaluation package checks outputs produced by public context strategies. It does not
implement an Agent loop and it does not ship private datasets or results.

## Safety and reproducibility

- API keys are read from environment variables and must never be committed.
- Reports bind the dataset hash, model IDs, sampling settings, strategy settings, and prompt
  contract version.
- Checkpoints reject incompatible resumes.
- `--max-provider-calls` places an explicit ceiling on paid calls.
- Generated reports and checkpoints belong in ignored local output directories.

## Minimal smoke test

```powershell
$env:CTXTTL_EVAL_API_KEY = "your-key"
$env:CTXTTL_EVAL_MODEL = "subject-model-id"
$env:CTXTTL_EVAL_JUDGE_MODEL = "judge-model-id"

ctxttl-eval benchmarks/ctxttlbench.jsonl `
  --base-url https://provider.example/v1 `
  --limit 1 `
  --strategy full_history `
  --max-provider-calls 2 `
  --output benchmark-results/smoke-report.json
```

The bundled fixture verifies integration mechanics only. It is not a public model leaderboard or
research result. Any external evaluation must document its own licensing, protocol, and claim
boundary outside this repository until intentionally released.
