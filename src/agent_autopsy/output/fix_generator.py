"""
Advanced fix suggestion generation.

Produces trace-tailored fix suggestions and patch snippets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_autopsy.preanalysis import PreAnalysisBundle
from agent_autopsy.preanalysis.suspects import Signal
from agent_autopsy.schema import Trace


@dataclass
class FixSuggestion:
    """A generated fix suggestion."""

    title: str
    category: str
    rationale: str
    patch_snippet: str
    event_ids: list[int]


class FixSuggestionGenerator:
    """Generate actionable, trace-aware fix suggestions."""

    def __init__(self, trace: Trace, preanalysis: PreAnalysisBundle):
        self.trace = trace
        self.preanalysis = preanalysis

    def generate(self) -> list[FixSuggestion]:
        suggestions: list[FixSuggestion] = []

        for signal in self.preanalysis.signals:
            builder = self._builder_for(signal)
            if builder is None:
                continue
            suggestions.append(builder(self, signal))

        # Deduplicate by title + category
        seen: set[tuple[str, str]] = set()
        deduped: list[FixSuggestion] = []
        for suggestion in suggestions:
            key = (suggestion.title, suggestion.category)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)
        return deduped

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialize suggestions to dictionaries."""
        return [
            {
                "title": suggestion.title,
                "category": suggestion.category,
                "rationale": suggestion.rationale,
                "patch_snippet": suggestion.patch_snippet,
                "event_ids": suggestion.event_ids,
            }
            for suggestion in self.generate()
        ]

    def _builder_for(self, signal: Signal) -> Callable[[FixSuggestionGenerator, Signal], FixSuggestion] | None:
        builders: dict[str, Callable[[FixSuggestionGenerator, Signal], FixSuggestion]] = {
            "infinite_loop": type(self)._fix_infinite_loop,
            "retry_storm": type(self)._fix_retry_storm,
            "hallucinated_tool": type(self)._fix_hallucinated_tool,
            "error_cascade": type(self)._fix_error_cascade,
            "context_overflow": type(self)._fix_context_overflow,
            "empty_response": type(self)._fix_empty_response,
            "timeout_pattern": type(self)._fix_timeout_pattern,
            "token_waste": type(self)._fix_token_waste,
            "auth_permission_failure": type(self)._fix_auth_permission_failure,
            "goal_drift": type(self)._fix_goal_drift,
            "stale_context": type(self)._fix_stale_context,
            "redundant_tool_call": type(self)._fix_redundant_tool_call,
            "inter_agent_failure": type(self)._fix_inter_agent_failure,
            "tool_contract_mismatch": type(self)._fix_tool_contract_mismatch,
        }
        builder = builders.get(signal.type)
        if builder is not None:
            return builder
        if signal.type.startswith("contract_"):
            if signal.type == "contract_missing_metadata":
                return type(self)._fix_contract_missing_metadata
            return type(self)._fix_tool_contract_mismatch
        return None

    def _fix_infinite_loop(self, signal: Signal) -> FixSuggestion:
        node = self._guess_loop_node(signal.event_ids)
        return FixSuggestion(
            title="Add max-iteration guard to looping node",
            category="code",
            rationale="Identical tool calls repeated consecutively indicate missing termination criteria.",
            patch_snippet=(
                f"# candidate node: {node}\n"
                "state['iteration_count'] = state.get('iteration_count', 0) + 1\n"
                "if state['iteration_count'] > MAX_ITERATIONS:\n"
                "    raise RuntimeError('Loop guard triggered')\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_hallucinated_tool(self, signal: Signal) -> FixSuggestion:
        tools_text = ", ".join(self.trace.env.tools_available) or "<declare tools>"
        return FixSuggestion(
            title="Harden system prompt with explicit tool allow-list",
            category="prompt",
            rationale="Tool hallucination can be reduced with explicit allowed tool names and policy.",
            patch_snippet=(
                "You may only call these tools:\n"
                f"{tools_text}\n"
                "If required functionality is unavailable, ask for guidance instead of inventing tools.\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_error_cascade(self, signal: Signal) -> FixSuggestion:
        root_tool = self._guess_root_error_tool(signal.event_ids)
        return FixSuggestion(
            title="Wrap failing tool calls with local error boundary",
            category="code",
            rationale="Error cascades indicate one failure propagates without containment.",
            patch_snippet=(
                f"def safe_{root_tool}(**kwargs):\n"
                "    try:\n"
                f"        return {root_tool}(**kwargs)\n"
                "    except Exception as exc:\n"
                "        return {'ok': False, 'error': str(exc), 'retryable': False}\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_context_overflow(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Add adaptive context windowing",
            category="ops",
            rationale="Context overflow indicates prompt assembly exceeds model limits.",
            patch_snippet=(
                "def build_context(history, max_tokens):\n"
                "    while estimate_tokens(history) > max_tokens:\n"
                "        history = summarize_oldest_chunk(history)\n"
                "    return history\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_retry_storm(self, signal: Signal) -> FixSuggestion:
        tool = self._guess_root_error_tool(signal.event_ids)
        return FixSuggestion(
            title=f"Add exponential backoff with jitter to {tool}",
            category="code",
            rationale=(
                "Repeated similar calls inside a short window indicate tight retry "
                "loops hammering a struggling dependency."
            ),
            patch_snippet=(
                "def call_with_backoff(fn, *args, max_attempts=5, base_delay=1.0):\n"
                "    for attempt in range(max_attempts):\n"
                "        try:\n"
                "            return fn(*args)\n"
                "        except TransientError:\n"
                "            delay = base_delay * (2 ** attempt)\n"
                "            time.sleep(delay + random.uniform(0, delay / 2))\n"
                "    raise RuntimeError('retry budget exhausted')\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_empty_response(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Validate model/tool output before accepting it",
            category="code",
            rationale="Empty responses propagate silently and starve downstream steps.",
            patch_snippet=(
                "result = step(state)\n"
                "if not result or result.get('output') in (None, '', []):\n"
                "    result = retry_with_adjusted_params(step, state)\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_timeout_pattern(self, signal: Signal) -> FixSuggestion:
        slowest = self._guess_root_error_tool(signal.event_ids)
        return FixSuggestion(
            title=f"Raise or make async the timeout budget around {slowest}",
            category="ops",
            rationale="Timeout patterns show calls exceed their deadline under normal load.",
            patch_snippet=(
                "# move slow calls out of the critical path\n"
                "future = executor.submit(run_step, state)\n"
                "try:\n"
                "    result = future.result(timeout=STEP_TIMEOUT_S)\n"
                "except FuturesTimeoutError:\n"
                "    result = {'ok': False, 'error': 'step timed out', 'partial': True}\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_token_waste(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Trim injected context and cap tool outputs",
            category="prompt",
            rationale=(
                "High token spend with little progress usually comes from oversized "
                "prompts or unbounded tool payloads re-entering the context."
            ),
            patch_snippet=(
                "MAX_TOOL_OUTPUT_CHARS = 2_000\n"
                "observation = run_tool(call)\n"
                "state['history'].append(clip(observation, MAX_TOOL_OUTPUT_CHARS))\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_auth_permission_failure(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Pre-flight credential and scope checks before dispatch",
            category="ops",
            rationale=(
                "Auth/permission failures repeated across events mean the run "
                "discovers missing access only after doing work."
            ),
            patch_snippet=(
                "def ensure_access(tool, required_scopes):\n"
                "    creds = load_credentials()\n"
                "    missing = required_scopes - creds.scopes\n"
                "    if missing:\n"
                "        raise PermissionError(f'{tool} lacks scopes: {missing}')\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_goal_drift(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Re-anchor the objective on every step",
            category="prompt",
            rationale="Similarity to the task goal decays when intermediate results displace the objective.",
            patch_snippet=(
                "TASK = state['original_goal']\n"
                "prompt = f\"Objective: {TASK}\\n\\nHistory:\\n{recent_history}\\n\"\n"
                "         \"Next single action toward the objective:\"\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_stale_context(self, signal: Signal) -> FixSuggestion:
        tool = self._guess_root_error_tool(signal.event_ids)
        return FixSuggestion(
            title=f"Invalidate cached {tool} results between steps",
            category="code",
            rationale=(
                "Reused context produced identical stale outputs; caches must be keyed "
                "on freshness, not just inputs."
            ),
            patch_snippet=(
                "cache_key = (tool_input, state['step_index'])\n"
                "if cache.last_key == cache_key:\n"
                "    cache.invalidate()\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_redundant_tool_call(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Memoize tool results by normalized input signature",
            category="code",
            rationale=(
                "Identical calls separated in time waste tokens and latency; a memo "
                "serves the earlier answer instead."
            ),
            patch_snippet=(
                "@memoize(key=lambda q: canonical(q))\n"
                "def search(q):\n"
                "    return backend.search(q)\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_inter_agent_failure(self, signal: Signal) -> FixSuggestion:
        return FixSuggestion(
            title="Validate handoff payloads between agents",
            category="code",
            rationale="Inter-agent failures trace to handoffs that skip schema validation.",
            patch_snippet=(
                "class Handoff(BaseModel):\n"
                "    task_id: str\n"
                "    payload: dict\n"
                "\n"
                "Handoff.model_validate(message)  # raises before the next agent starts\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_tool_contract_mismatch(self, signal: Signal) -> FixSuggestion:
        tool = self._guess_root_error_tool(signal.event_ids)
        declared = ", ".join(self.trace.env.tools_available) or "<none>"
        return FixSuggestion(
            title=f"Regenerate the JSON schema for {tool} and validate calls against it",
            category="tool",
            rationale=(
                f"The trace shows calls outside the declared contract (declared: {declared}); "
                "schema-first generation keeps implementations and declarations aligned."
            ),
            patch_snippet=(
                f"TOOL_SCHEMAS[{tool!r}] = {{\n"
                "    'name': ..., 'description': ...,\n"
                "    'parameters': {...},   # regenerate from the implementation\n"
                "}\n"
                "validate_call_against_schema(call, TOOL_SCHEMAS[call['name']])\n"
            ),
            event_ids=signal.event_ids,
        )

    def _fix_contract_missing_metadata(self, signal: Signal) -> FixSuggestion:
        tool = self._guess_root_error_tool(signal.event_ids)
        return FixSuggestion(
            title=f"Emit latency and token usage metadata from {tool}",
            category="ops",
            rationale=(
                "Calls without latency_ms/token_count cannot be costed or timed; "
                "instrument the wrapper so every observation carries both."
            ),
            patch_snippet=(
                "@instrument(latency=True, token_usage=True)\n"
                f"def traced_{tool}(**kwargs):\n"
                "    ...\n"
            ),
            event_ids=signal.event_ids,
        )

    def _guess_loop_node(self, event_ids: list[int]) -> str:
        for event_id in event_ids:
            event = self.trace.get_event(event_id)
            if event and event.name:
                return event.name
        return "router_or_loop_node"

    def _guess_root_error_tool(self, event_ids: list[int]) -> str:
        for event_id in event_ids:
            event = self.trace.get_event(event_id)
            if event and event.name:
                return event.name.replace("-", "_")
        return "tool_call"
