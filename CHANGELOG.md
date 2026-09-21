# Changelog

All notable public product changes are documented here. Private research work is intentionally
excluded from this repository and changelog.

## Unreleased

### Added

- Universal Agent identity coordinates across HTTP, Python, and MCP boundaries.
- Generic `POST /v1/context/compile` integration boundary.
- Independently packaged `ctxttl-mcp` lifecycle control plane.
- OpenAI-compatible Responses data plane and model discovery relay.
- Provider-reported usage and latency fields in execution traces.
- Multi-Agent isolation and intentional-sharing contract coverage.

### Fixed

- Streaming traces commit on protocol terminal events.
- Responses output capture handles providers that omit assistant messages from terminal snapshots.

## [0.2.1] - 2026-09-13

- Added English and Simplified Chinese documentation packs and locale validation.
- Corrected package and citation license metadata.

## [0.2.0] - 2026-09-13

- Added provider-neutral Agent context ports and execution telemetry.
- Added resumable provider-neutral evaluation utilities.
- Added repository hygiene checks and explicit publication boundaries.
- Strengthened lifecycle ingestion, transcript handling, and checkpoint identity validation.

## [0.1.1] - 2026-09-08

- Added request idempotency, ownership-safe context APIs, leases, replay, and authority-safe rendering.
- Added buffered and streaming assistant response capture and archive migration support.

## [0.1.0] - 2026-09-08

- Added the OpenAI-compatible Chat Completions proxy, Context IR, SQLite state projection,
  deterministic compilation, structured ingestion, archive retrieval, tracing, and CI.
