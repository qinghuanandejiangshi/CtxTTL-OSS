# Repository and evidence policy

CtxTTL Core is public software. Every tracked file must be safe for immediate redistribution.

## Tracked material

- source code, tests, examples, and provider-neutral fixtures;
- stable architecture, API, integration, security, and contribution documentation;
- deterministic fixtures and black-box contract tests for public product behavior.

## Material kept outside this repository

- downloaded or licensed third-party benchmark data;
- raw prompts, model responses, checkpoints, traces, and provider payloads;
- credentials, local databases, caches, virtual environments, and machine configuration;
- unpublished work, private fixtures, intermediate results, and internal notes.

## Evidence labels

The public repository makes product-contract claims only. Comparative model-quality, efficiency,
or research claims require a separately reviewed release decision and must never be inferred from
local development runs.

## Publication checks

Before each release, run lint, formatting, tests, link and locale checks, a current-tree credential
scan, and a Git-history credential scan. Never copy a private experiment directory wholesale into
this repository. Promote only reviewed files whose role and redistribution rights are explicit.
