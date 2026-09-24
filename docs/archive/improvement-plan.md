# Agent Autopsy - Improvement Plan

> **Archived 2026-05-06.** Superseded by [PLAN.md](../../PLAN.md) at the repo root. Kept for historical reference.

> A detailed roadmap to take Agent Autopsy from a working prototype to a best-in-class open source agent debugging tool.

---

## Table of Contents

1. [Current State Assessment](#current-state-assessment)
2. [Phase 1 - Clean Up What We Have](#phase-1---clean-up-what-we-have)
3. [Phase 2 - Deepen the Core](#phase-2---deepen-the-core)
4. [Phase 3 - MCP Server Integration](#phase-3---mcp-server-integration)
5. [Phase 4 - Open Source Growth](#phase-4---open-source-growth)
6. [Phase 5 - Advanced Features](#phase-5---advanced-features)
7. [Vision Statement](#vision-statement)

---

## Current State Assessment

### What Works Well
- The pipeline architecture (ingestion -> pre-analysis -> analysis -> output) is clean and logical
- Pattern detection (loops, retry storms, hallucinated tools, error cascades) is the strongest feature and genuinely useful
- The Pydantic schema is well-designed and flexible
- Graceful fallback from LLM analysis to deterministic mode when no API key is available
- The Streamlit GUI provides a usable interface for non-technical users
- The CLI gives power users a fast path

### What Needs Work
- Some advertised features are not actually implemented (LangChain/OpenTelemetry parsers, embeddings)
- Tests exist but many cannot actually fail, giving false confidence
- The LLM analysis agent is too simple and has no quality control on its output
- The web app is a single 900+ line file that mixes everything together
- Default model choice undermines the tool's value
- Hardcoded model limits will go stale over time
- No community infrastructure (contribution guide, issue templates, examples)

---

## Phase 1 - Clean Up What We Have

**Goal:** Remove dishonesty, fix broken things, make the foundation solid.

### 1.1 Remove Ghost Features

The project currently claims support for things that are not built. This is the fastest way to lose trust with users who try them and find they don't work.

**What to do:**
- Remove `sentence-transformers` from requirements.txt. It is not imported or used anywhere in the codebase but adds hundreds of megabytes to the install because it pulls in PyTorch. If we plan to use it later, add it back when the feature is actually built.
- For LangChain and OpenTelemetry parsers: be honest. Either mark them as "coming soon" in the README and docs, or remove the claims entirely until proper parsers are built. The format detection code can stay (it does no harm), but users should know that these formats will be handled by the generic fallback parser, which means some information may be lost.

### 1.2 Fix the Tests

Several tests use assertions like `assert len(results) >= 0` which always pass regardless of what the code does. A test that cannot fail is worse than no test because it creates the illusion of safety.

**What to do:**
- Go through every test file and replace meaningless assertions with real ones
- For example, the empty response detection test should use a trace that definitely has empty responses and assert that they are found (not that the count is >= 0)
- The error cascade test should assert specific behavior, not just "some number of results"
- Add negative tests: give the pattern detector a clean trace and assert it finds nothing
- Add edge case tests: empty trace, trace with one event, trace with thousands of events
- Make sure tests run in CI (GitHub Actions) so every pull request is validated

### 1.3 Fix Silent Error Handling

Throughout the codebase, there are bare `except:` blocks that swallow errors without logging them. For a tool whose entire purpose is debugging, this is a contradiction.

**What to do:**
- Replace bare `except:` with specific exception types
- Add logging (Python's `logging` module) so errors are recorded even if the user does not see them in the UI
- In the Streamlit app, when an error happens during batch analysis, show it clearly rather than silently falling back
- Consider adding an error log viewer in the GUI so users can see what went wrong under the hood

### 1.4 Clean Up Dependencies

The requirements.txt should only contain what the project actually uses.

**What to do:**
- Remove `sentence-transformers` (unused)
- Consider making `langgraph`, `langchain-core`, and `langchain-openai` optional dependencies (they are only needed for LLM analysis mode). Users who only want deterministic analysis should not need to install the entire LangChain ecosystem
- Add version upper bounds to prevent surprise breakage (e.g., `pydantic>=2.0,<3.0`)
- Consider using a `pyproject.toml` instead of `requirements.txt` for better dependency management and optional dependency groups

---

## Phase 2 - Deepen the Core

**Goal:** Make the things that already work, work much better.

### 2.1 Strengthen the LLM Analysis Agent

The current ReAct agent is minimal. It calls the LLM, runs any tools it requests, and generates a report. There is no quality control, no iteration budget, and no validation that the output is actually useful.

**What to do:**
- Add a configurable max iterations limit (not just "message count > 10")
- Add a quality gate between analysis and report generation. Before accepting a report, check: Does it contain event ID citations? Does it have a root cause? Does it have fix recommendations? If not, send it back to the LLM with feedback
- Add a report validator that scores the output on completeness (has all required sections), specificity (cites actual events, not vague claims), and actionability (fixes are concrete, not generic)
- Consider a two-pass approach: first pass does investigation (tool calls, data gathering), second pass does synthesis (report writing). This separation would produce better results than the current single-loop approach
- Add token budget awareness: track how many tokens have been used and warn if approaching limits, rather than just failing

### 2.2 Build Real Parsers for LangChain and OpenTelemetry

If we want to claim multi-format support, we need to deliver it.

**LangChain Parser:**
- Parse LangChain's callback-based trace format (run_type, parent_run_id, serialized data)
- Map LangChain's run types (chain, llm, tool, retriever) to our EventType enum
- Extract token usage from LLM runs
- Handle nested chains properly (parent-child relationships)

**OpenTelemetry Parser:**
- Parse the standard OTLP JSON format (resourceSpans, scopeSpans, spans)
- Map span attributes to our schema fields
- Extract trace ID and span ID for proper event linking
- Handle the semantic conventions for LLM observability (gen_ai.* attributes)
- This is especially valuable because many observability platforms export in OTLP format

**Additional Format Ideas:**
- CrewAI traces
- AutoGen conversation logs
- Raw OpenAI API logs (many developers just log API calls)
- Anthropic API logs
- A "bring your own format" plugin system where users can write a simple parser class

### 2.3 Add New Pattern Detectors

The current six patterns are a good start. There are more failure modes that agents commonly hit.

**New patterns to detect:**
- **Goal Drift**: The agent starts working on task A but gradually shifts to task B. Detect by comparing early actions/queries to later ones and measuring semantic divergence. This is where embeddings (sentence-transformers) would actually be useful - but only add the dependency when this feature is built.
- **Stale Context**: The agent keeps referencing outdated information from earlier in the conversation. Detect by finding when tool outputs change but the agent's behavior doesn't adapt.
- **Token Waste**: Large chunks of tokens spent on irrelevant tool calls or verbose outputs that don't contribute to the goal. Calculate a "useful token ratio."
- **Permission/Auth Failures**: Tool calls that fail due to authentication or permission issues, where the agent keeps retrying instead of escalating or changing approach.
- **Timeout Patterns**: Identify when the agent is slow not because of logic but because of slow external API calls, and which specific tools are the bottleneck.
- **Redundant Tool Calls**: The agent calls the same tool with the same input at different points in the trace (not consecutively, so not caught by loop detection) - it forgot it already has the answer.

### 2.4 Improve the Deterministic Report

When running without an LLM, the report is a simple list of signals and hypotheses. It could be much more useful.

**What to do:**
- Generate a proper narrative from the pattern detection results. Instead of just listing signals, connect them into a story: "The agent started normally but entered a loop at event 14, which caused a context overflow by event 46"
- Add a visual timeline (ASCII art or simple text-based) showing the flow of events with failure points marked
- Include automatic fix suggestions based on pattern type (these can be templated, no LLM needed). For example, if a loop is detected, the suggestion is always "add a max_iterations guard" with a specific recommendation for where to add it
- Calculate a "health score" for the trace: 0-100 based on what patterns were found, how severe they are, and what percentage of the trace was affected

### 2.5 Make Model Limits Configurable

The hardcoded dictionary of model context limits in patterns.py is already stale and will keep getting worse.

**What to do:**
- Move model limits to a configuration file (JSON or YAML) that users can update
- Ship a default config with common models but make it clear users should add their own
- Better yet, allow setting the context limit per-trace in the trace metadata, since the user knows what model they used
- Remove the hardcoded dictionary entirely

---

## Phase 3 - MCP Server Integration

**Goal:** Turn Agent Autopsy into an MCP (Model Context Protocol) server so any MCP-compatible client (Claude Code, Claude Desktop, Cursor, VS Code extensions, etc.) can use it as a live debugging tool.

### 3.1 Why MCP?

MCP is the right integration path because:
- It allows any AI assistant to use Agent Autopsy as a tool during development
- Developers could say "analyze this trace" or "debug my agent" directly in their IDE or chat
- It positions Agent Autopsy as infrastructure, not just a standalone app
- It creates a much larger surface area for adoption because every MCP client becomes a potential user

### 3.2 MCP Server Design

The MCP server should expose Agent Autopsy's capabilities as tools that an LLM can call.

**Core Tools to Expose:**

1. **analyze_trace**
   - Input: A trace file path or raw JSON trace data
   - Output: Full analysis report (signals, hypotheses, root causes, fixes)
   - Options: deterministic-only mode, model override, output format

2. **detect_patterns**
   - Input: Trace file path or raw JSON
   - Output: List of detected patterns with severity, evidence, and event IDs
   - This is the fast, deterministic-only version for quick checks

3. **validate_trace**
   - Input: Trace file path or raw JSON
   - Output: Whether the trace is valid, what format it is, and any parsing issues
   - Useful for developers building trace exporters

4. **get_trace_summary**
   - Input: Trace file path or raw JSON
   - Output: Quick stats (event count, error count, duration, tools used, status)
   - The lightweight "what happened" overview

5. **compare_traces**
   - Input: Two trace file paths
   - Output: Differences between the traces - what changed, what improved, what got worse
   - Very useful for before/after debugging

6. **capture_trace**
   - Input: Configuration for trace capture (directory, format, filters)
   - Output: Confirmation that tracing is set up, with the path where traces will be saved
   - Allows setting up trace capture from within the AI assistant

7. **list_traces**
   - Input: Optional directory path, optional filters (status, date range)
   - Output: Available traces with basic metadata
   - For browsing what traces exist

8. **get_event_details**
   - Input: Trace file path + event ID or event range
   - Output: Detailed information about specific events
   - For drilling into specific moments in a trace

9. **suggest_fixes**
   - Input: Trace file path or analysis results
   - Output: Categorized fix recommendations with templates
   - The actionable output that developers actually want

10. **health_check**
    - Input: Trace file path
    - Output: A simple health score (0-100) with a one-line summary
    - The fastest possible assessment

### 3.3 MCP Resources to Expose

Resources let the MCP client read data without calling a tool.

- **Recent traces**: List of recently captured/analyzed traces
- **Report archive**: Previously generated reports
- **Pattern catalog**: Documentation of all patterns the tool can detect (so the LLM knows what to look for)
- **Configuration**: Current settings so the LLM can suggest changes

### 3.4 MCP Prompts to Expose

Prompts are pre-built templates that the MCP client can offer to users.

- **"Debug my agent"**: A guided workflow that walks through trace selection, analysis, and fix recommendations
- **"Quick health check"**: Fast pattern scan with a yes/no "is this trace healthy?" answer
- **"Compare runs"**: Side-by-side comparison of two traces to understand what changed
- **"Explain this failure"**: Deep dive into a specific failure with root cause chain

### 3.5 How It Works In Practice

Imagine a developer using Claude Code or Cursor:

1. They run their agent and it fails
2. They say: "The agent failed again, can you check the trace?"
3. The AI assistant calls `list_traces` to find the latest trace
4. Calls `analyze_trace` on it
5. Gets back a detailed report with event citations and fix recommendations
6. The AI assistant can then directly apply the fixes to the code because it has the context of both the failure analysis and the codebase

This is dramatically more powerful than a standalone web app because the debugging tool is embedded in the development workflow. The developer never has to leave their editor.

### 3.6 MCP Implementation Approach

- Use the official MCP Python SDK to build the server
- The server should be installable as a standalone package (`pip install agent-autopsy`)
- Configuration via environment variables (same as current) or MCP config file
- The server should work with both stdio transport (for local tools like Claude Code) and SSE/HTTP transport (for remote setups)
- Keep the Streamlit app and CLI as separate interfaces to the same core - the MCP server is a third interface, not a replacement

---

## Phase 4 - Open Source Growth

**Goal:** Build the community and ecosystem around Agent Autopsy.

### 4.1 Project Identity and Positioning

Agent Autopsy should position itself clearly in the ecosystem. The tagline should make it instantly clear what it does and why someone should care.

**Positioning options:**
- "The debugger for AI agents" - simple, clear, broad
- "Post-mortem analysis for agent failures" - more specific, plays on the autopsy metaphor
- "Find out why your agent broke, and how to fix it" - benefit-focused

The README should lead with a real example: show a trace going in and a useful diagnosis coming out. Not feature lists - a before/after that makes people think "I need this."

### 4.2 Contribution Infrastructure

**What to add:**
- CONTRIBUTING.md with clear instructions on how to set up the development environment, run tests, and submit PRs
- Issue templates for bug reports, feature requests, and new pattern proposals
- PR template with a checklist (tests added, docs updated, no ghost features)
- A "good first issue" label strategy: identify 5-10 small, well-scoped tasks that newcomers can pick up
- A CHANGELOG.md that tracks what changed in each release
- A CODE_OF_CONDUCT.md (standard open source practice)

### 4.3 Documentation Overhaul

The current docs are internal-facing (they describe the system). They need to become user-facing (they help people use and extend the system).

**What to add:**
- A proper getting started guide with screenshots of the GUI
- A "How to capture traces from your agent" guide for each supported framework
- A "How to write a custom parser" guide for users who want to add their own format
- A "How to write a custom pattern detector" guide for users who want to detect new failure modes
- API documentation for the Python library (for users who want to embed Agent Autopsy in their own tools)
- A gallery of example traces with explanations: "Here's what a loop looks like. Here's what a hallucinated tool call looks like." This is hugely educational and helps users understand what the tool detects

### 4.4 Packaging and Distribution

**What to do:**
- Publish to PyPI as `agent-autopsy` so users can `pip install agent-autopsy`
- Create optional dependency groups: `pip install agent-autopsy[gui]` for Streamlit, `pip install agent-autopsy[llm]` for LangChain/LangGraph, `pip install agent-autopsy[mcp]` for MCP server
- The base install should be lightweight: just the schema, parsers, and pattern detection. No PyTorch, no LangChain, no Streamlit
- Add a Docker image for the full experience (GUI + MCP server + all parsers)
- Consider a Homebrew formula for Mac users: `brew install agent-autopsy`

### 4.5 Example Traces and Demo

Create a curated set of example traces that showcase every pattern the tool can detect:
- A clean, successful trace (to show the baseline)
- An infinite loop trace (the most common failure)
- A hallucinated tool call trace
- A context overflow trace
- An error cascade trace
- A retry storm trace
- A complex trace with multiple overlapping issues

Each example should come with a written explanation of what happened and what the tool finds. These serve double duty: they're great for demos and great for testing.

### 4.6 Branding and Visibility

- A simple landing page or GitHub Pages site with the example workflow
- A blog post or article explaining the problem Agent Autopsy solves (agent debugging is painful, here's how we fix it)
- Submit to awesome-langchain, awesome-llm-tools, and similar curated lists
- Consider submitting to MCP registries once the MCP server is built
- A short demo video (2-3 minutes) showing the full workflow: capture trace, upload to GUI, get diagnosis, apply fix

---

## Phase 5 - Advanced Features

**Goal:** Push the tool beyond basic pattern detection into genuinely intelligent debugging.

### 5.1 Semantic Analysis (Goal Drift Detection)

This is where `sentence-transformers` would actually be used. The idea: embed the task goal description and each agent action, then measure if the agent's actions are drifting away from the goal over time.

**How it would work:**
- Embed the task goal (from TaskContext)
- Embed each LLM response and tool call input
- Calculate cosine similarity between goal and each action
- If similarity drops below a threshold over a window of events, flag as "goal drift"
- Show a drift chart in the GUI (similarity score over time)

Only add this when it's actually built and tested. Do not add the dependency before the feature.

### 5.2 Trace Comparison and Regression Detection

Agents often work fine, then break after a change. The ability to compare two traces and highlight what changed is extremely valuable.

**What to build:**
- Side-by-side trace comparison: same events, different outcomes
- Diff view: what tool calls changed, what LLM responses changed
- Regression detection: "this trace has a new loop that the previous trace didn't have"
- Performance comparison: "this trace used 3x more tokens than the baseline"

### 5.3 Live Trace Monitoring

Instead of analyzing traces after the fact, watch an agent run in real time.

**What to build:**
- A WebSocket-based live view in the Streamlit app
- Real-time pattern detection as events come in (stream analysis)
- Alerts when a pattern is detected mid-run (e.g., "loop detected, you may want to stop this agent")
- Integration with the TraceSaver to push events to the live view as they happen

### 5.4 Fix Generation

Go beyond recommending fixes to actually generating them.

**What to build:**
- For loop detection: generate a code patch that adds a max_iterations guard to the specific graph node that's looping
- For hallucinated tools: generate an updated system prompt that explicitly lists available tools
- For error cascades: generate error handling code for the specific tool that started the cascade
- For context overflow: generate a context management strategy (summarization, windowing) tailored to the specific trace

These generated fixes should be presented as suggestions that the user can review and apply, not automatically applied.

### 5.5 Multi-Agent Trace Support

Modern agent systems often involve multiple agents collaborating. Agent Autopsy should handle this.

**What to build:**
- Parse traces that contain multiple agent identifiers
- Visualize inter-agent communication and handoffs
- Detect failure patterns that span agents (e.g., agent A sends bad data to agent B, causing B to fail)
- Show a "conversation flow" view that tracks how information moves between agents

### 5.6 Benchmark and Evaluation Mode

Help users understand not just if their agent failed, but how well it's performing over time.

**What to build:**
- Track metrics across multiple runs: success rate, average token usage, average latency, common failure patterns
- Dashboard showing trends over time
- Alerting when metrics degrade (e.g., "success rate dropped from 90% to 60% this week")
- Export metrics for integration with external monitoring tools (Grafana, Datadog)

### 5.7 Plugin System

Let the community extend Agent Autopsy without modifying the core.

**Plugin types:**
- Custom parsers (new trace formats)
- Custom pattern detectors (new failure patterns)
- Custom report templates (different output formats)
- Custom fix generators (framework-specific fixes)
- Custom visualizations (new GUI views)

Each plugin type should have a simple interface (abstract base class) and a registration mechanism. Ship with a "how to write a plugin" guide and a template repository.

---

## Vision Statement

Agent Autopsy should become the standard tool that every AI agent developer reaches for when something goes wrong. Not just a trace viewer, but an intelligent debugging companion that understands agent behavior, detects patterns human eyes would miss, and provides actionable fixes.

The path there is:
1. **Be honest** about what works and what doesn't (Phase 1)
2. **Be excellent** at the core use case of trace analysis (Phase 2)
3. **Be everywhere** through MCP integration (Phase 3)
4. **Be community-driven** through open source best practices (Phase 4)
5. **Be intelligent** through advanced analysis features (Phase 5)

The MCP server is the most important strategic move. It transforms Agent Autopsy from "a tool you have to go to" into "a tool that's always available where you work." When a developer can say "debug my agent" inside their IDE and get a full autopsy report, that's when the tool becomes indispensable.

---

*Last updated: April 2026*
*Status: Planning*
