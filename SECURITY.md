# Security policy

CtxTTL `0.2.1` is an early research MVP, not a supported production service.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed credential. Use the
[repository's private security advisory form](https://github.com/qinghuanandejiangshi/CtxTTL-OSS/security/advisories/new).
This repository-scoped private channel is the maintained security contact. Include the affected
revision, impact, reproduction steps, and any safe mitigation you have identified. Do not include
real user data or active credentials.

## Operational boundaries

- Identity headers are isolation coordinates, not authentication or authorization.
- SQLite is intended for local research and controlled evaluation, not untrusted multi-tenant
  deployment.
- Trace content capture and conversation archives can contain prompts, tool data, personal data,
  or secrets. Keep them disabled or access-controlled unless they are required.
- Retrieved content is untrusted evidence. Rendering boundaries reduce accidental instruction
  escalation but cannot guarantee prompt-injection resistance.
- Store provider credentials only in environment variables or an untracked `.env` file. Rotate any
  credential that appears in logs, traces, reports, commits, or chat transcripts.
- Put authentication, authorization, TLS, request limits, retention, backup, and deletion controls
  in front of CtxTTL before any production-style exposure.

See [known limitations](docs/known-limitations.md) for the complete research boundary.
