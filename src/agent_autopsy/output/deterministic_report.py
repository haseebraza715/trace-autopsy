"""
Deterministic (no-LLM) autopsy report: what / where / evidence / likely cause.
"""

from __future__ import annotations

import json

from agent_autopsy.preanalysis import PreAnalysisBundle
from agent_autopsy.schema import Trace, TraceEvent

PATTERN_DESCRIPTIONS: dict[str, str] = {
    "infinite_loop": "The same tool call with the same inputs repeats without progress.",
    "retry_storm": "A tool is invoked many times in a short window, often after failures.",
    "context_overflow": "Estimated tokens exceed the model or trace context budget.",
    "hallucinated_tool": "The agent called a tool name that is not in the declared tool set.",
    "empty_response": "An LLM or tool returned an empty or effectively empty response.",
    "error_cascade": "Multiple errors chained; later steps failed because earlier ones did.",
    "goal_drift": "Semantic similarity to the stated goal dropped over the run.",
    "stale_context": "Old assumptions or context stayed in play after the situation changed.",
    "token_waste": "High token usage with little measurable progress toward the goal.",
    "auth_permission_failure": "HTTP 401/403 or auth/permission-related error text appeared.",
    "timeout_pattern": "Timeouts or deadline-exceeded errors, or unusually slow steps.",
    "redundant_tool_call": "Duplicate or near-duplicate tool calls with similar inputs.",
    "inter_agent_failure": "Multi-agent handoff or coordination looks broken.",
    "tool_contract_mismatch": "Tool output does not match declared contract or schema.",
}

LIKELY_CAUSE: dict[str, str] = {
    "infinite_loop": "Missing exit condition, bad router, or reward for repeating the same action.",
    "retry_storm": "Upstream dependency flaking; missing circuit breaker or backoff.",
    "context_overflow": "Unbounded history or oversized payloads passed to the model.",
    "hallucinated_tool": "Prompt allows free-form tools or schema drift vs runtime registry.",
    "empty_response": "Model/tool returned nothing; check temperature, truncation, or API errors.",
    "error_cascade": "Insufficient isolation; one error poisons downstream steps.",
    "goal_drift": "Weak goal anchoring or long context washing out the objective.",
    "stale_context": "Cache or memory not invalidated after tool results change state.",
    "token_waste": "Verbose reasoning or repeated planning without new information.",
    "auth_permission_failure": "Wrong key, expired token, or insufficient OAuth scope.",
    "timeout_pattern": "Slow network or unbounded work in a tool; tune timeouts and limits.",
    "redundant_tool_call": "No memoization or idempotency; planner repeating work.",
    "inter_agent_failure": "Handoff protocol or shared state contract is incomplete.",
    "tool_contract_mismatch": "Schema or validation mismatch between tool impl and declaration.",
}


# A contract violation that restates a pattern finding on the same events.
CONTRACT_COUNTERPART: dict[str, str] = {
    "contract_unknown_tool": "hallucinated_tool",
    "contract_invalid_input": "tool_contract_mismatch",
    "contract_invalid_output": "tool_contract_mismatch",
}


def _duplicate_note(sig, signals) -> str | None:
    """Label a contract signal whose events another finding already cites."""
    counterpart = CONTRACT_COUNTERPART.get(sig.type)
    events = set(sig.event_ids or [])
    if not counterpart or not events:
        return None
    for other in signals:
        if other.type == counterpart and events <= set(other.event_ids or []):
            noun = "event" if len(events) == 1 else "events"
            title = counterpart.replace("_", " ").title()
            return (
                f"- **Note:** Same {noun} as the {title} finding above; "
                f"the health score counts each event once."
            )
    return None


def _event_by_id(trace: Trace, event_id: int) -> TraceEvent | None:
    for ev in trace.events:
        if ev.event_id == event_id:
            return ev
    return None


def _snippet_for_event(trace: Trace, event_id: int, max_chars: int = 400) -> str:
    ev = _event_by_id(trace, event_id)
    if ev is None:
        return f"(event {event_id} not found)"
    parts: list[str] = [f"type={ev.type.value}"]
    if ev.name:
        parts.append(f"name={ev.name}")
    if ev.agent_id:
        parts.append(f"agent={ev.agent_id}")
    blob = " | ".join(parts)
    payload = {
        "input": ev.input,
        "output": ev.output,
        "error": getattr(ev, "error", None),
    }
    try:
        extra = json.dumps(payload, default=str, ensure_ascii=False)
    except Exception:
        extra = str(payload)
    if len(extra) > max_chars:
        extra = extra[: max_chars - 3] + "..."
    return f"{blob}\n{extra}"


def _evidence_block(trace: Trace, event_ids: list[int], lines: int = 5) -> str:
    ids = sorted(set(event_ids))[:lines]
    blocks: list[str] = []
    for eid in ids:
        blocks.append(f"**Event {eid}**\n```\n{_snippet_for_event(trace, eid)}\n```")
    return "\n\n".join(blocks)


def render_deterministic_markdown(trace: Trace, preanalysis: PreAnalysisBundle) -> str:
    """Rich markdown report without any LLM."""
    lines: list[str] = [
        f"# Autopsy Report (deterministic): {trace.run_id}",
        "",
        "## Summary",
        "",
        f"- **Status:** {trace.status.value}",
        f"- **Events:** {len(trace.events)} | **Errors (stats):** {trace.stats.num_errors}",
        "",
        preanalysis.summary,
        "",
        "## Findings",
        "",
    ]

    if not preanalysis.signals:
        lines.extend(
            [
                "No deterministic failure patterns matched this trace.",
                "",
            ]
        )
    else:
        for sig in preanalysis.signals:
            desc = PATTERN_DESCRIPTIONS.get(
                sig.type,
                "Pattern detected by static analysis of the trace.",
            )
            where = ", ".join(str(e) for e in (sig.event_ids or [])[:12]) or "n/a"
            if len(sig.event_ids or []) > 12:
                where += ", …"
            likely = LIKELY_CAUSE.get(
                sig.type,
                "Review the cited events and surrounding tool/LLM steps (heuristic).",
            )
            note = _duplicate_note(sig, preanalysis.signals)
            lines.extend(
                [
                    f"### {sig.type.replace('_', ' ').title()} ({sig.severity})",
                    "",
                    f"- **What:** {desc}",
                    f"- **Where (event IDs):** {where}",
                    *([note] if note else []),
                    "",
                    "**Evidence (trace excerpts)**",
                    "",
                    _evidence_block(trace, sig.event_ids or []),
                    "",
                    f"**Likely cause (heuristic):** {likely}",
                    "",
                    "---",
                    "",
                ]
            )

    lines.extend(
        [
            "## Hypotheses",
            "",
        ]
    )
    if not preanalysis.hypotheses:
        lines.append("_No hypotheses generated._")
    else:
        for hyp in preanalysis.hypotheses:
            lines.append(
                f"- **{hyp.description}** — {hyp.confidence:.0%} confidence ({hyp.category}); "
                f"events {hyp.supporting_events}"
            )
            if hyp.suggested_fixes:
                for fx in hyp.suggested_fixes:
                    lines.append(f"  - {fx}")

    lines.extend(
        [
            "",
            "---",
            "*Generated by Agent Autopsy deterministic analysis only (no LLM).*",
        ]
    )
    return "\n".join(lines)
