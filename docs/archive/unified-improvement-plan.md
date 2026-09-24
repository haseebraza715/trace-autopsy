# Agent Autopsy — Unified Improvement Plan

> **Archived 2026-05-06.** Superseded by [PLAN.md](../../PLAN.md) at the repo root. Kept for historical reference.

A consolidated, prioritized plan combining critical review findings with concrete, actionable fixes. Ordered by impact and sequenced so early work unblocks later work.

---

## Phase 1 — Stop the Bleeding (Week 1)

These are foundational issues. Fix before adding any new features.

### 1.1 Replace silent exception handling
**Problem:** Bare `except Exception:` blocks scattered across the codebase swallow failures with no logging.
- [src/preanalysis/patterns.py:98-102](../../src/preanalysis/patterns.py) — plugin failures disappear
- [src/ingestion/parser.py:38](../../src/ingestion/parser.py) — format detection errors ignored
- [src/cli.py:99](../../src/cli.py) — CLI swallows root causes
- [src/analysis/agent.py:238-246](../../src/analysis/agent.py) — LLM errors leave state inconsistent

**Fix:**
- Define specific exception types: `ParseError`, `SchemaValidationError`, `PluginError`, `LLMError`.
- Replace bare `except Exception` with targeted catches that log with traceback.
- Surface parser errors with event context (e.g. `"Failed at event 12: missing 'type' field"`).

**Effort:** 2–3 days. **Impact:** Critical. Unblocks all future debugging.

---

### 1.2 Fix packaging — split `requirements.txt`
**Problem:** `requirements.txt` bundles `langgraph`, `langchain-openai`, `mcp`, `streamlit`, `pytest` as hard deps. A CLI user installs 500 MB for no reason. `pyproject.toml` already has optional groups — `requirements.txt` ignores them.

**Fix:**
- Delete `requirements.txt` and point users to `pip install "agent-autopsy[full]"` / `[cli]` / `[gui]` / `[mcp]`.
- Or split into `requirements-core.txt`, `requirements-gui.txt`, `requirements-mcp.txt`, `requirements-dev.txt`.
- Update README install section accordingly.

**Effort:** 10 minutes. **Impact:** High. Removes biggest friction for new users.

---

### 1.3 Gate the embedding model on config
**Problem:** [src/preanalysis/patterns.py:677](../../src/preanalysis/patterns.py) lazy-loads a 1.5 GB sentence-transformers model via `@lru_cache` even when semantic drift detection is disabled or no goal is set.

**Fix:**
- Only load the embedding model when `semantic_drift_enabled=True` **and** a goal is present.
- Add a `--no-embeddings` CLI flag and default it to true if no goal is provided.

**Effort:** 2 hours. **Impact:** High. Cuts memory footprint drastically for common usage.

---

## Phase 2 — Architecture (Week 2)

### 2.1 Extract a unified facade API
**Problem:** [src/cli.py](../../src/cli.py) imports from 7+ internal modules directly. Streamlit and MCP reach into internals independently. Any refactor ripples across all entry points.

**Fix:**
- Create `src/api.py` exposing: `analyze(trace_path, options) -> Report`, `detect_patterns(...)`, `generate_report(...)`.
- CLI, Streamlit, and MCP all import *only* from `src/api.py`.
- Target: cli.py goes from 10+ imports → 1.

**Effort:** 3–5 days. **Impact:** High. Enables isolated testing and decouples UI from core.

---

### 2.2 Break up the god-file `app.py`
**Problem:** 33 KB single-file Streamlit app. All pages, render functions, and state logic in one place. Unmaintainable.

**Fix:**
- Create `src/ui/` package with per-page modules: `home.py`, `analyze.py`, `batch.py`, `reports.py`.
- Use Streamlit's native multi-page routing via `pages/` directory.
- `app.py` becomes a ~50-line entry/router.

**Effort:** 2–3 days. **Impact:** High. Makes UI maintainable and testable.

---

### 2.3 Abstract the LLM provider
**Problem:** [src/utils/config.py](../../src/utils/config.py) and prompts are hardcoded to OpenRouter via `langchain-openai`. No way to use Anthropic, Gemini, or Ollama without forking.

**Fix:**
- Introduce `PROVIDER=openrouter|anthropic|ollama|openai` env var.
- Use LangChain's `init_chat_model()` which handles provider dispatch.
- Update `.env.example` with provider options.

**Effort:** 1 day. **Impact:** High. Opens the tool to the broader ecosystem.

---

## Phase 3 — Testing (Week 3)

### 3.1 End-to-end integration tests
**Problem:** 71 test functions for ~9500 LOC, all unit tests. No test runs the full `ingestion → pre-analysis → analysis → output` pipeline. A breaking change in chaining goes uncaught.

**Fix:**
- Create `tests/test_e2e.py` that runs `autopsy analyze examples/<trace>.json` for each supported format (LangGraph, LangChain, OpenTelemetry, generic JSON).
- Assert report contains expected sections and detected patterns.

**Effort:** 1–2 days. **Impact:** High.

---

### 3.2 Negative / error-path tests
**Problem:** No tests for corrupted JSON, missing API keys, LLM timeouts, malformed traces, or plugin failures.

**Fix:**
- Add `tests/test_error_paths.py` covering: truncated JSON, wrong format detection, API key missing, LLM timeout mock, plugin raising.
- Add tests for the LLM agent control flow in [src/analysis/agent.py](../../src/analysis/agent.py) — budget enforcement, iteration caps, state transitions.

**Effort:** 2 days. **Impact:** High.

---

### 3.3 MCP server tests
**Problem:** [tests/test_mcp_service.py](../../tests/test_mcp_service.py) has ~9 tests for a 642-line service.

**Fix:**
- Add mock-client tests covering all MCP tools/resources/prompts.
- Test auth flow (see 4.2 below).

**Effort:** 1 day. **Impact:** Medium.

---

## Phase 4 — User Experience (Week 4)

### 4.1 Stream LLM output in the UI
**Problem:** LLM analysis takes 30–90s. The UI blocks with a spinner — no token streaming, no live thinking, no partial results. Biggest UX complaint for LLM tools in 2026.

**Fix:**
- Use `st.write_stream()` with LangChain's `astream_events()` to stream tokens live.
- Show the agent's current step (which tool it's calling, token budget remaining).

**Effort:** 2 hours (basic), 1 day (with step display). **Impact:** High.

---

### 4.2 MCP authentication
**Problem:** [src/mcp/server.py](../../src/mcp/server.py) exposes analysis tools over SSE with zero auth. Any local process can call `analyze_trace` on potentially sensitive traces.

**Fix:**
- Token-based auth for SSE transport (env var or config).
- Document in [docs/mcp.md](mcp.md) as a security note.
- stdio transport can remain unauthenticated (process-local).

**Effort:** 1 day. **Impact:** Medium (high if anyone deploys SSE).

---

### 4.3 Custom Streamlit theme
**Problem:** Default purple Streamlit theme. Looks like every other prototype.

**Fix:**
- Create `.streamlit/config.toml` with `primaryColor`, `backgroundColor`, `font` matching a dark forensic aesthetic (black + amber/red severity).
- Matches the "autopsy" brand.

**Effort:** 15 minutes. **Impact:** Medium. Professional polish.

---

### 4.4 Structured LLM output for quality scoring
**Problem:** [src/analysis/agent.py](../../src/analysis/agent.py) `ReportQualityValidator` uses naive regex on raw markdown. LLM can write "Root Cause: unknown" and score 100%. Citation regex `r"\bEvent(?:s)?\s+\d+"` is brittle.

**Fix:** (Simpler than the originally suggested AST + embeddings)
- Have the LLM emit a JSON schema for the report (root cause, evidence events, recommendations) instead of free markdown.
- Validate structurally with Pydantic.
- Render markdown from the validated object.

**Effort:** 1–2 days. **Impact:** Medium.

---

## Phase 5 — Documentation & Polish (Week 5)

### 5.1 Align docs with reality
**Problem:** [ARCHITECTURE.md](../../ARCHITECTURE.md) is 38 lines and skips token budgeting, contract validation, embedding fallback, plugin loading — all present in the code. README undersells pattern detection (actually ~13 detectors, not 6).

**Fix:**
- Expand [ARCHITECTURE.md](../../ARCHITECTURE.md) with the real pipeline, including token budget math and quality thresholds.
- Update README feature table to list all 13+ patterns.
- Add `src/DESIGN.md` explaining *why* (deterministic-first, token estimation, contract validation).

**Effort:** 1–2 days. **Impact:** Medium. Builds trust.

---

### 5.2 README restructure + demo GIF
**Problem:** README has 13 sections and buries the value prop. No demo GIF — the single biggest trust-builder for a dev tool.

**Fix:**
- Restructure: hero (one-sentence value + demo GIF) → 30-second quickstart → feature table → rest.
- Record a 15-second GIF of `autopsy analyze examples/simple_loop.json` producing a report.
- Add `--no-llm` flag to the first example so people can try without an API key.

**Effort:** 1 hour. **Impact:** High for adoption.

---

### 5.3 Expand pattern detection (cautiously)
**Problem:** Pattern coverage may be narrower than competitors. But: before adding more, verify the existing ~13 are documented and surfaced.

**Fix:**
- Step 1: audit [src/preanalysis/patterns.py](../../src/preanalysis/patterns.py) and document every detector in [docs/patterns.md](patterns.md).
- Step 2: if gaps remain, add: tool argument schema drift, state mutation corruption, missing handoff conditions, silent hallucination (output cites nonexistent events).
- Do **not** add patterns without matching tests.

**Effort:** 1 day audit + 2 days per new pattern. **Impact:** Medium.

---

## Quick Wins (Do Today — <2 hours total)

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| Q1 | Split `requirements.txt` → optional groups | 10 min | High |
| Q2 | Add `.streamlit/config.toml` with custom theme | 15 min | Medium |
| Q3 | Add `--no-llm` flag to README's first example | 5 min | High |
| Q4 | Record and embed demo GIF in README | 30 min | High |
| Q5 | Gate embedding model on config flag | 2 hrs | High |

---

## What This Plan Deliberately Does NOT Do

- **No new patterns before auditing existing ones.** The README undersells coverage; documentation may be the real gap.
- **No AST + embedding-similarity quality scorer.** Overkill — structured LLM output is the cleaner fix.
- **No big rewrite.** Each phase is sequenced so the app remains shippable throughout.
- **No feature expansion in Phases 1–3.** Harden first.

---

## Success Criteria

By end of Phase 3:
- Zero bare `except Exception:` blocks in the codebase.
- CLI install size < 100 MB for core usage.
- One `src/api.py` facade, imported by all three entry points.
- E2E test coverage for all 4 trace formats.
- Memory footprint < 200 MB when `--no-embeddings`.

By end of Phase 5:
- Streaming UI, auth-protected MCP SSE, themed Streamlit, GIF in README.
- Docs match code. Pattern detector list accurate.
