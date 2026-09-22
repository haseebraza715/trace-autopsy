# Changelog

All notable changes to this project are documented in this file.

The format follows Keep a Changelog and semantic versioning intent.

## [Unreleased]

### Added

- Reproduce mode: `autopsy replay trace.json --reproduce [--reproduce-n N]` re-issues every LLM call against the configured provider and reports whether the original failure patterns still fire on the synthetic trace. Exit 1 when patterns persist across every run (regression check). Pluggable invoker for tests; new module `src/advanced/reproduce.py`.
- Code-aware fix diffs: when `--code-root <path>` is provided, fix artifacts include applyable `<pattern>.patch` files (unified diff). Currently supports `infinite_loop`, `retry_storm`, `hallucinated_tool` templates. Source files are located via OTel `code.filepath`/`code.lineno` metadata (or LangChain dialect equivalents). New `src/output/code_context.py` plus `--code-root`/`--write-patches` flags on the `analyze` and `fixes` commands.
- OpenTelemetry parser now speaks the [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) natively, plus the OpenInference and traceloop/openllmetry dialects. Spans from all three SDKs normalize to identical canonical events (model, input, output, token split). Cross-SDK fixtures + parametrized test in `tests/test_otel_genai.py`.
- Per-direction token counts (`input_tokens`/`output_tokens`) are preserved in event metadata so the cost detector prices LLM calls accurately instead of using a fallback split.
- Published per-detector precision/recall in `docs/patterns.md` (Measured accuracy section). `scripts/eval_detectors.py --markdown-out PATH` regenerates the table from the labeled corpus.
- CONTRIBUTING.md: new detectors must add ≥5 corpus entries (≥3 positives, ≥2 negatives) before review.
- CI gate: `autopsy analyze --fail-on <patterns>` (comma-separated) exits 1 only when listed patterns match — `eslint --max-warnings 0` for agents. `autopsy diff --fail-on-regression` exits 1 when the candidate has a pattern the baseline did not.
- GitHub composite action at `.github/actions/agent-autopsy/action.yml` (installs autopsy, runs the gate over a glob, posts a PR summary comment). Copy-paste examples for GitHub Actions and GitLab CI in `examples/ci/`.
- README now leads with a "Use in CI" section before the Streamlit UI section.
- Cost section in every report (run cost, wasted spend, daily extrapolation, per-model breakdown). New `src/preanalysis/pricing.py` ships a pricing table for ~20 popular models. CLI flag `--no-cost` to hide it. JSON output adds a `cost` block.
- `PLAN.md` at repo root as the single live planning document (supersedes three plan docs in `docs/`)
- `AGENTS.md` at repo root (renamed from local-only `AGENT.MD`, now tracked per the standard convention)
- Real-trace corpus under `tests/fixtures/real_traces/` with `scripts/eval_detectors.py` (CI gate)
- `autopsy watch`, `autopsy replay`, `autopsy diff` (alias of `compare`), richer trace diff output
- Deterministic report sections (what / where / evidence / likely cause) and `text` report format
- LLM disk cache (`~/.cache/agent-autopsy/`), optional `--stream` / `--no-cache` / `--provider` on `analyze`
- Structured JSON appendix for LLM synthesis with Pydantic validation (`src/analysis/structured_report.py`)
- Opt-in CLI telemetry (`autopsy telemetry on|off|status`, env `AUTOPSY_TELEMETRY=1`)
- Docs: `docs/launch-post.md`, `docs/demo-gif.md`, `docs/good-first-issues.md`, `ROADMAP.md`
- Scripts: `scripts/benchmark_no_llm.py`, `scripts/record_demo.sh`, `scripts/render_demo_gif.py` (README GIF)
- `docs/images/autopsy-demo.gif` — deterministic `autopsy analyze` demo asset
- PyPI publish workflow on version tags (`.github/workflows/publish.yml`)
- MCP server interface with tools, resources, and prompts (`src.mcp`)
- Streamable HTTP/SSE/stdio MCP transport support
- Advanced deterministic detectors and configurable model context limits
- Analysis quality gate with iterative report revision feedback
- Deterministic report health score and richer timeline rendering

### Changed

- Archived 5 historical planning docs to `docs/archive/` (improvement-plan, unified-improvement-plan, v2-best-in-class-plan, implementation-audit, phase-status). `PLAN.md` is the active plan.

### Fixed

- OpenTelemetry `_extract_final_output` no longer matches `llm.token_count.completion` (or other token-count attrs whose key contains "completion"/"response") when picking the final output. Previously triggered a Pydantic validation error when an int token count was returned where a string/dict was expected.
- LangGraph stack moved to `src/analysis/llm_agent.py` so `--no-llm` avoids importing LangChain
- CLI `analyze` defaults to `-f text`; exit code `2` for parse/tool errors
- `ReportGenerator` always surfaces deterministic markdown body in saved reports
- Parser depth improved for LangChain and OpenTelemetry traces
- Phase roadmap tracker updated through Phase 4

## [0.4.0] - 2026-04-09

### Added

- Phase 1, 2, and 3 roadmap deliverables
- CI workflow for pytest
- MCP docs and service-level tests

### Changed

- Improved docs around parser support and fallback behavior
- Improved error handling and logging visibility
