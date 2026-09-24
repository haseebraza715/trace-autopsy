# Plan

Living plan for the next push on Agent Autopsy. Six work items, ordered by ROI. Each is independently shippable — pick one, finish it, move on. Update this file as items land.

This file supersedes [docs/improvement-plan.md](docs/improvement-plan.md), [docs/unified-improvement-plan.md](docs/unified-improvement-plan.md), and [docs/v2-best-in-class-plan.md](docs/v2-best-in-class-plan.md). Item 0 archives them.

---

## 0. Consolidate planning docs — DONE 2026-05-06

**What:** Stop having overlapping plan docs. One source of truth.

**Done:**
- Archived 5 docs to [docs/archive/](docs/archive/): `improvement-plan.md`, `unified-improvement-plan.md`, `v2-best-in-class-plan.md`, plus the two execution-tracking sidecars `implementation-audit.md` and `phase-status.md`. Each got an archive banner pointing here. Internal `../` links rewritten to `../../`.
- Renamed `AGENT.MD` → `AGENTS.md`, removed the `.gitignore` entry so it tracks publicly per the [agents.md convention](https://agents.md/).
- [README.md:373](README.md) "Roadmap and plans" row now points to `PLAN.md` and `ROADMAP.md`.
- [CHANGELOG.md](CHANGELOG.md) Unreleased section updated.
- [docs/gui_plan.md](docs/gui_plan.md) intentionally kept (forward-looking GUI design, not historical planning).

---

## 1. Labeled detector corpus + published precision/recall — DONE 2026-05-06

**What:** Move detectors from "heuristic that fires" to "measured signal with known accuracy."

**Done:**
- Discovered an existing labeled corpus already in [tests/fixtures/real_traces/](tests/fixtures/real_traces/) with 21 traces and a `_manifest.yaml` schema (entries with `must_include`/`clean`/`skip_eval` fields). Re-used instead of building a parallel `labeled_corpus/`.
- Extended [scripts/eval_detectors.py](scripts/eval_detectors.py) with `--markdown-out PATH` to regenerate a per-detector table.
- Published the baseline accuracy table in [docs/patterns.md](docs/patterns.md) under "Measured accuracy" — 9 detectors at 100% recall and 100% precision against the corpus. The other 5 detectors lack labeled positives and are explicitly called out.
- [CONTRIBUTING.md](CONTRIBUTING.md): new detectors must add ≥5 corpus entries (≥3 positives, ≥2 negatives) and refresh the `docs/patterns.md` table.
- Existing [.github/workflows/tests.yml](.github/workflows/tests.yml) already runs `python scripts/eval_detectors.py` with the global thresholds (recall ≥80%, precision ≥90%), so the CI gate is live.

**Deferred:** Per-detector precision floors (set to "current value minus 5pp" per detector) — not added because all detectors currently sit at 100% and a global floor of 90% precision is already enforced. Worth revisiting when corpus expands and numbers drift.

**Spinoff:** Corpus expansion to ~50 traces is the next high-ROI follow-up (would unlock measurement for `goal_drift`, `stale_context`, `inter_agent_failure`, `retry_storm`, `tool_contract_mismatch`).

---

## 2. CI-gate positioning + GitHub Action — DONE 2026-05-06

**What:** Reposition Agent Autopsy as the `eslint --max-warnings 0` for agents. Code is mostly there; the *positioning* isn't.

**Done:**
- `autopsy analyze --fail-on <pat1,pat2>` in [src/cli.py](src/cli.py): exit 1 only when a listed pattern matches; default behavior (fail on any finding) preserved when flag is absent.
- `autopsy diff --fail-on-regression` in [src/cli.py](src/cli.py): exit 1 when patterns appear in trace B that aren't in trace A. Works with both `text` and `json` output formats.
- Composite action at [.github/actions/agent-autopsy/action.yml](.github/actions/agent-autopsy/action.yml): installs autopsy, iterates over the trace glob, posts a markdown summary (per-trace status + matched patterns) to `$GITHUB_STEP_SUMMARY` and as a PR comment via `actions/github-script`.
- Copy-paste snippets at [examples/ci/github-actions.yml](examples/ci/github-actions.yml) and [examples/ci/gitlab-ci.yml](examples/ci/gitlab-ci.yml).
- README now has "Use in CI" section above the Streamlit UI section ([README.md:256](README.md)).
- Self-dogfood: [.github/workflows/tests.yml](.github/workflows/tests.yml) runs autopsy over example traces and asserts the regression gate passes on identical traces.
- 7 new CLI tests in [tests/test_cli_gates.py](tests/test_cli_gates.py); full suite is 152 green (was 145).

**Deferred:** publishing the composite action to the GitHub Marketplace requires a release tag on the repo — owner action.

---

## 3. OpenTelemetry GenAI semantic conventions, first-class — DONE 2026-05-06

**What:** Speak fluent `gen_ai.*` OTel conventions, not just generic OTel JSON.

**Done:**
- Added `_extract_gen_ai_fields` to [src/ingestion/formats/opentelemetry.py](src/ingestion/formats/opentelemetry.py): tries OTel GenAI keys first, then OpenInference (`llm.*`, `input.value`, `output.value`), then traceloop (`ai.*`). Returns canonical (name, input, output, token_count). Generic keyword matching remains as a fallback.
- `_determine_span_type` now uses `gen_ai.operation.name` (chat/completion/tool_use/...) as the primary signal before falling back to span-name keyword inference.
- Per-direction tokens preserved in `event.metadata` as `input_tokens`/`output_tokens` aliases so the pricing module can compute accurate dollar costs instead of using its 70/30 fallback.
- Three fixtures in [tests/fixtures/otel_genai/](tests/fixtures/otel_genai/): `otel_native_chat.json`, `openinference_chat.json`, `traceloop_chat.json`. All three encode the same logical chat call (gpt-4o, 120 input + 30 output tokens, "What is 2+2?").
- Parametrized test [tests/test_otel_genai.py](tests/test_otel_genai.py) asserts all three fixtures collapse to identical canonical events (model, type, token counts, input, output, computed cost). 22 new test cases.
- **Bug fixed in flight:** `_extract_final_output` was matching `llm.token_count.completion` because the key contains "completion", returning an int where a str/dict was expected. Now skips token/usage keys.
- README "Supported inputs" callout + new section in [docs/ingestion.md](docs/ingestion.md#opentelemetry-format) with the canonical → dialect mapping table.
- Full suite: 174 green (was 152).

---

## 4. Cost surface on every report — DONE 2026-05-06

**What:** Surface `$ wasted on this run` and extrapolated rate. The line item that gets the tool funded.

**Done:**
- New [src/preanalysis/pricing.py](src/preanalysis/pricing.py): ~20 model pricing entries (OpenAI, Anthropic 3.x/4.x, Gemini 2.x, free/self-hosted Gemma/Llama/Mistral/Qwen). Substring-matching `match_model` handles `openai/`, `anthropic/`, `google/` provider prefixes and `:free`/`:nitro` OpenRouter suffixes; longest-match wins so `gpt-4o-mini` beats `gpt-4`.
- `event_token_split` reads OTel-GenAI `gen_ai.usage.*` keys, OpenInference `prompt_tokens`/`completion_tokens` from event metadata; falls back to a 70/30 split of `token_count` for traces without per-direction counts.
- `compute_trace_cost(trace, waste_event_ids)` returns total + waste + per-model breakdown + waste ratio + daily extrapolation. `waste_event_ids_from_signals` collects the right events from `token_waste`/`retry_storm`/`infinite_loop`/`redundant_tool_call` signals.
- Cost section now renders in both [src/output/deterministic_report.py](src/output/deterministic_report.py) and [src/output/report.py](src/output/report.py); JSON output gets a `cost` block.
- CLI: `--no-cost` flag added to both `analyze` and `autopsy-run`. Plumbed through `src/api.py:generate_report` and `run_deterministic_analysis`.
- 25 new tests in [tests/test_pricing.py](tests/test_pricing.py); full suite is 145 green (was 120).
- README "What you get" lists cost breakdown + `--no-cost`.

---

## 5. Code-aware fix suggestions — DONE 2026-05-06

**What:** Move from "we suggest you add a retry limit" to "here's the diff to apply."

**Done:**
- New [src/output/code_context.py](src/output/code_context.py): `find_source_location` reads OTel `code.filepath`/`code.lineno`/`code.function` from event metadata (plus LangChain `source_file`/`lineno` and friends). `SourceLocation.resolve(code_root)` handles absolute paths, code-root-relative paths, and basename rglob fallback.
- `build_unified_diff` and `insert_lines_at` produce `git apply`-clean patches with matched indentation and a trailing newline.
- [src/output/fix_generator.py](src/output/fix_generator.py) extended: each `FixSuggestion` now carries `source_locations` and (when `code_root` is provided + a template exists) `patch_diff`. Diff templates in place for `infinite_loop`, `retry_storm`, `hallucinated_tool`. New `write_patches(output_dir)` method emits `<pattern>.patch` files.
- CLI: `--code-root` flag added to `analyze` and `fixes`; `fixes` also gets `--write-patches DIR`. Both surface the source locations and indicate when a unified diff is available.
- 14 new tests in [tests/test_code_aware_fixes.py](tests/test_code_aware_fixes.py), including an end-to-end test that runs `git apply` on the generated patch and asserts the file contains the guard.
- README "Core workflows" section now shows the `--code-root` invocation.
- Full suite: 188 green (was 174).

**Deferred:** Same-trace re-run regression check (the original step 4 of "apply patch and confirm same failure doesn't reproduce") — this overlaps Item 6's `--reproduce` scope, will land there.

---

## 6. Replay → re-execute (regression testing surface) — DONE 2026-05-06

**What:** Bridge debugging into regression testing. Re-run LLM calls and check if the failure recurs.

**Done:**
- New [src/advanced/reproduce.py](src/advanced/reproduce.py) with `Reproducer` and `ReproductionResult`. Re-issues every LLM call in the trace, builds a synthetic trace with the new outputs, runs `PatternDetector` on it, and classifies original patterns into **persistent** (fired on every run — failure not in the LLM output) vs **cleared** (cleared by all runs — failure was an LLM-output artifact).
- Pluggable `LLMInvoker` callable: real implementation reuses the same provider abstraction as the analysis agent (OpenRouter/OpenAI/Anthropic/Ollama via `init_chat_model`). Tests inject a mock invoker — no real provider needed.
- CLI: `autopsy replay trace.json --reproduce [--reproduce-n N]` in [src/cli.py:543](src/cli.py). Refuses to run without provider credentials. Exit code 1 when patterns persist across every run (regression), 0 otherwise.
- Per-event preview of new outputs (or invoker error) printed alongside the summary line.
- 8 new tests in [tests/test_reproduce.py](tests/test_reproduce.py) covering persistent/cleared classification, partial reproduce rates, error handling, dict-input serialization, model override.
- README "Replay" workflow now shows the reproduce invocations + exit-code semantics.
- Full suite: 196 green (was 188).

**Deferred:** Tool-call re-execution. Today only LLM calls are replayed; tool calls and decisions are not re-driven (re-driving the agent end-to-end is a much bigger lift). For most failure modes the LLM-only signal is enough — if the pattern is in the tool layer, it stays in `persistent_patterns` and you know to fix the agent code, not the prompt.

---

## Sequencing

Reasonable two-week order:

1. Item 0 (consolidation) — clears the workspace
2. Item 4 (cost surface) — small, ships fast, immediate visible value
3. Item 2 (CI gate) — repositioning win, mostly already-built code
4. Item 1 (labeled corpus) — start the corpus labeling early since it's the long pole; can run in parallel with the others
5. Items 3, 5, 6 — pick based on which inbound questions / issues land first

Items 1, 5, 6 are independent enough that contributors can take them in parallel.

## Out of scope (still)

Per [ROADMAP.md:18](ROADMAP.md): no hosted SaaS, no VS Code extension, no plugin marketplace, no chat-over-trace UI. All six items above respect those constraints.
