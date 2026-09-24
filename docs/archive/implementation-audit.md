# Implementation Audit — Plans vs. Reality

> **Archived 2026-05-06.** Superseded by [PLAN.md](../../PLAN.md) at the repo root. Kept for historical reference.

Cross-check of [unified-improvement-plan.md](unified-improvement-plan.md) (v1) and [v2-best-in-class-plan.md](v2-best-in-class-plan.md) against the code on `main` as of 2026-04-18.

Legend: ✅ shipped · 🟡 partial · ❌ missing

---

## v1 — Unified Improvement Plan

### Phase 1 — Foundation

| Item | Status | Evidence |
|------|--------|----------|
| 1.1 Typed exceptions replacing bare `except` | ✅ | [src/errors.py](../../src/errors.py) defines `ParseError`, `SchemaValidationError`, `PluginError`, `LLMError`; imported in [src/cli.py:26](../../src/cli.py) |
| 1.2 Split `requirements.txt` via `pyproject` extras | ✅ | `[cli]`, `[llm]`, `[gui]`, `[mcp]`, `[embeddings]`, `[dev]`, `[full]` groups in [pyproject.toml](../../pyproject.toml) |
| 1.3 Gate embedding model on config flag | ✅ | `--no-embeddings` CLI flag and `config.skip_embeddings` in [src/cli.py:89-126](../../src/cli.py) |

### Phase 2 — Architecture

| Item | Status | Evidence |
|------|--------|----------|
| 2.1 Unified facade API | ✅ | [src/api.py](../../src/api.py) exposes `Report`, `run_analysis`, `stream_llm_analysis_text`, `llm_credentials_configured` |
| 2.2 Break up god-file `app.py` | ✅ | `app.py` is now 18 lines; UI lives in [src/ui/streamlit_pages.py](../../src/ui/streamlit_pages.py) |
| 2.3 LLM provider abstraction | ✅ | `llm_credentials_configured` dispatches `openrouter / openai / anthropic / ollama` |

### Phase 3 — Testing

| Item | Status | Evidence |
|------|--------|----------|
| 3.1 E2E integration tests | ✅ | [tests/test_e2e.py](../../tests/test_e2e.py) |
| 3.2 Negative / error-path tests | ✅ | [tests/test_error_paths.py](../../tests/test_error_paths.py), [tests/test_ollama_optional.py](../../tests/test_ollama_optional.py) |
| 3.3 MCP server tests | 🟡 | [tests/test_mcp_service.py](../../tests/test_mcp_service.py) exists; auth path coverage still thin |

### Phase 4 — UX

| Item | Status | Evidence |
|------|--------|----------|
| 4.1 Stream LLM output in UI | ✅ | `stream_llm_analysis_text` in api; Streamlit uses `stream_mode` |
| 4.2 MCP SSE auth | ✅ | `_StaticMcpTokenVerifier` in [src/mcp/server.py:19](../../src/mcp/server.py); reads `MCP_SSE_TOKEN` |
| 4.3 Custom Streamlit theme | ✅ | [.streamlit/config.toml](../../.streamlit/config.toml) — amber (#e8a317) on black, matches forensic aesthetic |
| 4.4 Structured LLM output (Pydantic) | ✅ | [tests/test_structured_report.py](../../tests/test_structured_report.py), [tests/test_citation_validate.py](../../tests/test_citation_validate.py) |

### Phase 5 — Docs & polish

| Item | Status | Evidence |
|------|--------|----------|
| 5.1 Align docs with reality | 🟡 | [ARCHITECTURE.md](../../ARCHITECTURE.md) still terse; [docs/architecture.md](architecture.md) has the detail |
| 5.2 README restructure + demo GIF | ✅ | README rewritten today; GIF regenerated at 1280×720 with larger type |
| 5.3 Document all patterns | ✅ | [docs/patterns.md](patterns.md) |

**Phase 1–5 summary: 13/15 ✅, 2 partial. No outright gaps.**

---

## v2 — Best-in-Class CLI Plan

### Week 1 — Dogfood foundation

| Item | Status | Evidence |
|------|--------|----------|
| Real trace corpus + manifest | ✅ | [tests/fixtures/](../../tests/fixtures), [examples/traces/](../../examples/traces) |
| Detector accuracy harness | ✅ | [scripts/eval_detectors.py](../../scripts/eval_detectors.py) |
| Precision/recall threshold in CI | 🟡 | Script exists; verify CI fails on regression |

### Week 2 — Deterministic fast path

| Item | Status | Evidence |
|------|--------|----------|
| Sub-second `--no-llm` | ✅ | [scripts/benchmark_no_llm.py](../../scripts/benchmark_no_llm.py) |
| Colorized output + NO_COLOR | ✅ | Rich console throughout `cli.py` |
| Exit codes documented | ✅ | [src/cli.py:1-6](../../src/cli.py) docstring |
| `--format=json\|text\|markdown` | ✅ | `-f` flag in analyze command |

### Week 3 — Daily drivers

| Item | Status | Evidence |
|------|--------|----------|
| `autopsy watch <dir>` | ✅ | `watch` command in [src/cli.py](../../src/cli.py) |
| `autopsy diff a b` (+ `compare` alias) | ✅ | Uses [src/advanced/comparison.py](../../src/advanced/comparison.py) |
| `autopsy replay --from --speed` | ✅ | `replay` command |
| Shell completion | ✅ | `add_completion=True` on Typer app |

### Week 4 — LLM path earns its seconds

| Item | Status | Evidence |
|------|--------|----------|
| Structured Pydantic output | ✅ | `test_structured_report.py` |
| Citation validation against trace | ✅ | `test_citation_validate.py` |
| Streaming with step display | ✅ | `stream_llm_analysis_text` in api |
| LLM disk cache | ✅ | [src/analysis/llm_cache.py](../../src/analysis/llm_cache.py), `--no-cache` flag |
| Ollama first-class | ✅ | `test_ollama_optional.py`, provider dispatch in api |

### Week 5 — Trust & distribution

| Item | Status | Evidence |
|------|--------|----------|
| Demo GIF | ✅ | [docs/images/autopsy-demo.gif](images/autopsy-demo.gif) — regenerated with bigger type |
| README restructured | ✅ | Rewritten today |
| Per-pattern docs | ✅ | [docs/patterns.md](patterns.md) |
| Contributor on-ramp | ✅ | [docs/good-first-issues.md](good-first-issues.md), [CONTRIBUTING.md](../../CONTRIBUTING.md) |
| PyPI publish | ❌ | `agent-autopsy` not yet on PyPI. Only editable installs work today |

### Week 6 — Launch & iterate

| Item | Status | Evidence |
|------|--------|----------|
| Launch post draft | ✅ | [docs/launch-post.md](launch-post.md) |
| Roadmap | ✅ | [ROADMAP.md](../../ROADMAP.md) |
| Opt-in telemetry | ✅ | `autopsy telemetry on`, [tests/test_telemetry.py](../../tests/test_telemetry.py) |
| Actual launch (HN / Reddit / X) | ❌ | Not yet posted |
| ≥ 1 external issue | ❌ | Gated on launch |

**v2 summary: 22/25 ✅, 1 partial, 2 genuine gaps (PyPI + launch).**

---

## What's left to do

1. **Publish to PyPI.** Wire a GitHub Actions tag-push job, bump to `0.4.0 → 1.0.0`, verify `pip install agent-autopsy` on a clean venv. (½ day)
2. **Tighten MCP auth test coverage.** Add a test that SSE without a token is rejected and with the correct token is accepted. (2 hrs)
3. **Confirm eval_detectors is CI-enforced.** If not already, add the job to fail PRs when precision/recall drops below threshold. (1 hr)
4. **Collapse [ARCHITECTURE.md](../../ARCHITECTURE.md) duplication.** Either expand the top-level file or make it a pointer to [docs/architecture.md](architecture.md). (30 min)
5. **Ship the launch post.** Everything else is ready — this is now a marketing task, not an engineering one.

Nothing else from either plan is missing. The project is effectively feature-complete against both plans.
