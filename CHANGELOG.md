# Changelog

All notable changes to this project are documented in this file.

The format follows Keep a Changelog and semantic versioning intent.

## [Unreleased]

## [0.5.0] - 2026-08-22

### Added

- Hand-labeled negative-control trace fixtures (`neg_*`) and positive-control fixtures for previously unrepresented detectors (`pos_*`: retry storm, goal drift, stale context, inter-agent failure); `must_not_include` support in the detector manifest and evaluator with forbidden-detection enforcement
- Real-trace corpus (`tests/fixtures/real_traces/`) with `scripts/eval_detectors.py` as a CI regression gate
- `autopsy watch`, `autopsy replay`, and `autopsy diff` (alias of `compare`) with richer trace diff output
- Deterministic report sections (what / where / evidence / likely cause) and the `text` report format
- LLM disk cache (`~/.cache/agent-autopsy/`) plus optional `--stream`, `--no-cache`, and `--provider` flags on `analyze`
- Structured JSON appendix for LLM synthesis with Pydantic validation
- Opt-in CLI telemetry (`autopsy telemetry on|off|status`, env `AUTOPSY_TELEMETRY=1`)
- PyPI publish workflow on version tags (`.github/workflows/publish.yml`)
- MCP server interface with tools, resources, and prompts; stdio, SSE, and streamable HTTP transports
- Advanced deterministic detectors and configurable model context limits
- Analysis quality gate with iterative report revision feedback
- Report health score and richer timeline rendering
- Fix suggestions for every deterministic signal type via a builder registry, including contract violations and missing-instrumentation metadata
- Docs (`docs/launch-post.md`, `docs/good-first-issues.md`, `ROADMAP.md`), demo scripts, and regenerated demo assets

### Changed

- Deterministic detectors require failure evidence: timeouts flag slow calls only on errored or failed runs; loops and redundant-tool-call patterns require a failed run or errored events; auth/permission signals stay silent when the only evidence is benign prompt/output language
- CLI exit gate treats a recovered error with zero signals as a clean run
- Detector corpus metrics are framed as corpus-relative regression results, not external accuracy
- LangGraph wiring lives behind the analysis layer so `--no-llm` avoids importing LangChain
- CLI `analyze` defaults to `-f text`
- LangChain and OpenTelemetry parsers accept deeper trace shapes
- The synthesized report appends only the deterministic narrative's per-finding detail instead of embedding a second full report body
- Detector hot paths are linear-time: incremental retry-storm clustering, memoized tool signatures, and constant-time token-waste neighbors

### Fixed

- Auth/permission language inside prompts or outputs of healthy runs no longer fabricates a HIGH finding (and no longer flips CI gates to red)
- Retry storms no longer chain across out-of-order exporter timestamps, and a second burst of the same tool is reported instead of dropped
- Valid exports carrying explicit JSON nulls (`status`, `role`, `llm_output`, ...) or numeric OTLP attributes parse instead of failing schema validation; `"error": null` can no longer flip a healthy run to FAILED
- Empty event-less traces are rejected as a tool error instead of reporting SUCCESS at 100/100 health
- Parse failures exit `2` (not `1`) in `summary`, `validate`, `fixes`, and `agent-flow`, completing the documented 0/1/2 contract
- Reports contain a single title and status line; JSON output carries the detailed narrative (`detailed_analysis`)
- LLM analysis works against OpenRouter: model IDs are no longer sent with a stale `openai:` prefix that caused 400s on every call
- The MCP server refuses to start on SSE/HTTP transports without `MCP_SSE_TOKEN`
- Cached analyses and saved traces are written atomically, so crashes cannot leave truncated files and concurrent writers cannot clobber each other

## [0.4.0] - 2026-04-09

### Added

- Phase 1, 2, and 3 roadmap deliverables
- CI workflow for pytest
- MCP docs and service-level tests

### Changed

- Improved docs around parser support and fallback behavior
- Improved error handling and logging visibility

[unreleased]: https://github.com/haseebraza715/trace-autopsy/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/haseebraza715/trace-autopsy/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/haseebraza715/trace-autopsy/releases/tag/v0.4.0
