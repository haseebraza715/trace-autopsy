# Agent Autopsy v2 — Best-in-Class Local CLI Plan

> **Archived 2026-05-06.** Superseded by [PLAN.md](../../PLAN.md) at the repo root. Kept for historical reference.

**Prerequisite:** [unified-improvement-plan.md](unified-improvement-plan.md) is complete. That plan hardens the codebase; this plan makes it the tool people actually reach for.

**Target:** A senior engineer debugging an agent failure at 2am reaches for `autopsy` before LangSmith, before `grep`, before their homegrown script.

**Timeline:** 6 weeks, sequential. Each week has an exit criterion — do not move on until it's met.

---

## Guiding Principles

1. **Local-first is the moat.** Never require an account, never require network.
2. **Deterministic path must be sub-second.** LLM is optional polish.
3. **The tool you'd use daily.** Dogfood every week on real traces.
4. **Fail loud, fail clear.** No silent degradation ever.
5. **Don't chase LangSmith.** Win on speed, privacy, zero-setup.

---

## Week 1 — Dogfood Foundation

**Goal:** Prove the existing patterns actually fire on real traces. Tune until they do.

### 1.1 Build a real trace corpus
- Collect ≥ 20 real agent traces: own projects, public LangGraph examples, GitHub issues with attached traces, HuggingFace agent traces.
- Categorize each by known failure mode (infinite loop, hallucination, tool error, context overflow, etc.) or "clean run."
- Store in `tests/fixtures/real_traces/` with a `_manifest.yaml` mapping file → expected detections.

### 1.2 Detector accuracy harness
- New script: `scripts/eval_detectors.py` that runs every detector against the corpus and reports precision/recall per pattern.
- CI job runs this and fails if any detector drops below threshold (start at 80% recall, 90% precision).

### 1.3 Fix broken detectors
- Expect 3–6 detectors to have problems. Tune thresholds, fix false-positives.
- Delete any detector that can't hit the threshold — better no detector than a noisy one.

**Exit criteria:**
- Corpus of ≥ 20 real traces with ground-truth labels.
- Every shipped detector hits ≥ 80% recall / 90% precision.
- `scripts/eval_detectors.py` runs in CI.

---

## Week 2 — The Deterministic Fast Path

**Goal:** `autopsy analyze trace.json --no-llm` runs in under 1 second on a 10 MB trace and produces output worth reading.

### 2.1 Profile and optimize
- Profile the cold-start path with `py-spy` or `cProfile`.
- Fix the top 3 hot spots. Common culprits: JSON parsing, Pydantic validation on every event, regex recompilation.
- Lazy-import anything not on the no-LLM path (`langchain_*`, `sentence_transformers`, `streamlit`).

### 2.2 Improve no-LLM report quality
- The deterministic report currently probably says "found N issues." Make it say:
  - **What** happened (pattern name + one-line human description)
  - **Where** (event IDs, file:line if trace carries source info)
  - **Evidence** (3–5 lines of surrounding trace context)
  - **Likely cause** (static heuristic, clearly marked as heuristic)
- Template this. Don't use an LLM.

### 2.3 Colorized output + exit codes
- Severity colors: red (critical), yellow (warn), dim (info). Detect TTY, disable on pipe.
- Exit codes: `0` clean, `1` issues found, `2` tool error. Document in README.
- `--format=json|text|markdown` flag. Default to text for humans, JSON for CI.

**Exit criteria:**
- `autopsy analyze big_trace.json --no-llm` runs in < 1 second.
- Output is scannable in a terminal and pipe-friendly (`| grep`, `| jq`).
- Exit codes documented and tested.

---

## Week 3 — The Daily Driver Features

**Goal:** Features that make people run `autopsy` dozens of times a day.

### 3.1 `autopsy watch <dir>`
- Uses `watchdog` to monitor a directory for new `.json` traces.
- On new file: auto-analyzes, prints summary, optionally plays a terminal bell on critical findings.
- Deduplicates — don't re-analyze the same file twice.
- This is the killer feature for dev loops where an agent writes traces to disk.

### 3.2 `autopsy diff trace_a.json trace_b.json`
- Compares two traces. Highlights:
  - Events present in one, absent in other
  - Patterns detected in one, not the other
  - Tool calls with different arguments
  - Timing deltas per step
- This is genuinely unique — LangSmith doesn't do this well.

### 3.3 `autopsy replay trace.json --from <event_id>`
- Prints the trace step-by-step with pauses, like a debugger.
- `--speed 2x`, `--step` for interactive stepping, `--until <pattern>`.
- Useful for understanding *what the agent was thinking*.

### 3.4 Shell completion
- Generate bash/zsh/fish completions. Package them. One-line install instruction in README.

**Exit criteria:**
- You personally use `watch` or `diff` at least once during the week on a real debugging task.
- All three commands have tests + docs.

---

## Week 4 — LLM Path That Earns Its Seconds

**Goal:** When the user opts into LLM analysis, the extra 30 seconds is clearly worth it.

### 4.1 Structured output (not regex-scored markdown)
- LLM returns JSON matching a Pydantic schema: `RootCause`, `Evidence[]`, `Recommendations[]`, `Confidence`.
- Render markdown from the object. Validates structurally.
- Citation `event_ids` are validated against the actual trace — hallucinated events caught automatically.

### 4.2 Streaming with step display
- `autopsy analyze --stream` streams tokens + shows current tool call ("calling get_event(42)…").
- Same in Streamlit via `st.write_stream()`.

### 4.3 LLM cache
- Hash (trace_digest, prompt_version, model) → cached response on disk (`~/.cache/agent-autopsy/`).
- Re-running the same analysis is free and instant.
- `--no-cache` flag to bypass.

### 4.4 Local model first-class
- Test + document Ollama path. `autopsy analyze --provider ollama --model llama3.1:8b`.
- If it works well on small traces, this removes the biggest adoption barrier (no API key).

**Exit criteria:**
- LLM never cites a non-existent event (validator catches it).
- Re-running the same analysis hits cache < 100ms.
- Ollama path documented and tested end-to-end.

---

## Week 5 — Trust and Distribution

**Goal:** A stranger finds the repo and is using it within 2 minutes.

### 5.1 Demo GIF
- 15 seconds. Terminal. Paste trace → get report → highlight the bug it found.
- Use `asciinema` → `agg` for crisp output. Embed at the top of README.
- This is the single highest-ROI 30 minutes of the whole plan.

### 5.2 README restructure
Order:
1. One-sentence hero + GIF
2. 30-second quickstart (`pip install`, `autopsy analyze example.json`)
3. What it detects (table with all ~13 patterns, one-line description each)
4. Why local-first
5. Rest (MCP, plugins, dev)

### 5.3 Publish to PyPI
- `agent-autopsy` on PyPI via GitHub Actions on tag push.
- Semantic versioning. Start at `0.1.0`.
- Verify `pip install agent-autopsy && autopsy analyze example.json` works on a fresh venv.

### 5.4 Document every pattern
- [docs/patterns.md](patterns.md): for each detector, one section with: what it catches, example trace snippet, false-positive scenarios, how to tune.
- Users tune detectors only if they understand them.

### 5.5 Contributor on-ramp
- 5 "good first issue" tickets seeded (new pattern ideas, small CLI polish).
- `CONTRIBUTING.md` has a "add a new pattern in 15 minutes" tutorial.

**Exit criteria:**
- `pip install agent-autopsy` works on a clean machine.
- README has a GIF above the fold.
- Every pattern has docs.

---

## Week 6 — Launch and Iterate

**Goal:** Get the tool in front of the audience who needs it.

### 6.1 Write the launch post
- Title: "Agent Autopsy: a local-first CLI for debugging LangGraph/LangChain traces"
- Structure: the problem (debugging agents is painful), the approach (deterministic-first), one real bug the tool caught with screenshots, install command.
- 600–900 words. Include the GIF.

### 6.2 Launch surfaces
- Show HN, r/LocalLLaMA, r/LangChain, X/Twitter, LangChain Discord.
- Don't spam — pick 2–3 surfaces, engage with comments for 48 hours.

### 6.3 Feedback triage
- Every issue filed in launch week gets a response within 24 hours.
- Real bug reports get fixed that week.
- Log feature requests in a `ROADMAP.md` but don't commit to them yet.

### 6.4 Observability on itself
- Add opt-in, anonymous usage telemetry: which commands run, which detectors fire. Off by default, prompt once.
- This data tells you what to build in v3.

**Exit criteria:**
- Launch post published.
- ≥ 10 stars from people you don't know.
- ≥ 1 issue filed by a stranger.
- A plan for week 7+ driven by real feedback, not guesses.

---

## The Three Metrics That Matter

Track these from Week 1. They're the only things that tell you if the plan is working:

1. **Detector precision/recall on real traces** — Week 1 harness. Goal: stays above 80/90.
2. **Cold-start time on a 10 MB trace, no LLM** — Week 2 target. Goal: < 1s. Never let it regress.
3. **Unique users in launch month** — PyPI download stats. Goal: 100+. (Low bar intentionally — real validation is repeat users, which you can't measure in month 1.)

---

## What You Will Be Tempted to Build, But Shouldn't

- **A hosted SaaS version.** Kills your differentiator. Say no to every request.
- **A VSCode extension.** Maybe in v3. Not now — CLI first.
- **Support for 10 more trace formats.** Unless a real user files an issue, the 4 you have are enough.
- **A plugin marketplace.** Zero users are asking for this. Build when someone submits 3 plugins.
- **An LLM-powered interactive chat over the trace.** Cool demo, questionable utility, huge scope creep.

---

## Definition of Done (end of week 6)

- `pip install agent-autopsy` → working CLI in < 30 seconds.
- `autopsy analyze trace.json` → useful report in < 1 second, no API key needed.
- `autopsy analyze trace.json --llm` → excellent report in < 60 seconds, streamed, cached.
- `autopsy watch`, `autopsy diff` exist and work.
- README has a GIF. PyPI page looks legit. Docs describe every pattern.
- 80/90 detector accuracy, maintained in CI.
- ≥ 1 stranger has filed an issue.

That's best-in-class local CLI for agent trace debugging.
