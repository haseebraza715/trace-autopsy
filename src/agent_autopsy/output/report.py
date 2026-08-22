"""
Report generation for Agent Autopsy.

Generates structured markdown reports from analysis results.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_autopsy.analysis.agent import AnalysisResult
from agent_autopsy.plugins import get_plugin_manager
from agent_autopsy.schema import Trace


def markdown_to_plain(md: str) -> str:
    """Strip common markdown markers for terminal / pipe-friendly text."""
    lines: list[str] = []
    for line in (md or "").splitlines():
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        lines.append(line)
    return "\n".join(lines)


@dataclass
class AutopsyReport:
    """Structured autopsy report."""
    run_id: str
    status: str
    generated_at: datetime
    summary: str
    timeline: list[str]
    root_cause_chain: list[str]
    fix_recommendations: dict[str, list[str]]
    confidence: float
    health_score: int
    evidence_events: list[int]
    raw_report: str
    preanalysis: dict = field(default_factory=dict)
    trace_summary: dict = field(default_factory=dict)


class ReportGenerator:
    """
    Generates autopsy reports from analysis results.

    Supports multiple output formats (markdown, JSON).
    """

    def __init__(self, trace: Trace, analysis_result: AnalysisResult):
        self.trace = trace
        self.result = analysis_result

    def generate(self) -> AutopsyReport:
        """Generate the autopsy report."""
        return AutopsyReport(
            run_id=self.trace.run_id,
            status=self.trace.status.value,
            generated_at=datetime.now(),
            summary=self._extract_summary(),
            timeline=self._extract_timeline(),
            root_cause_chain=self._extract_root_causes(),
            fix_recommendations=self._extract_fixes(),
            confidence=self._extract_confidence(),
            health_score=self._calculate_health_score(),
            evidence_events=self._extract_evidence_events(),
            raw_report=self.result.report,
            preanalysis=self.result.preanalysis,
            trace_summary=self.result.trace_summary,
        )

    def _extract_summary(self) -> str:
        """Extract summary from report."""
        report = self.result.report
        if "## Summary" in report:
            start = report.find("## Summary")
            end = report.find("##", start + 10)
            if end == -1:
                end = len(report)
            return report[start:end].replace("## Summary", "").strip()

        signals = self.result.preanalysis.get("signals", [])
        if not signals:
            return f"Run {self.trace.run_id} completed with no high-risk deterministic signals."

        first_signal = signals[0]
        signal_type = str(first_signal.get("type", "issue")).replace("_", " ")
        impacted = len(set(self._extract_evidence_events()))
        total = max(1, len(self.trace.events))
        return (
            f"Run {self.trace.run_id} shows a primary '{signal_type}' failure pattern. "
            f"{impacted}/{total} events were directly implicated by deterministic evidence."
        )

    def _extract_timeline(self) -> list[str]:
        """Generate a deterministic timeline from the trace (headline events)."""
        timeline = []
        evidence = set(self._extract_evidence_events())

        max_events = 20
        for event in self.trace.events[:max_events]:
            marker = "!"
            if event.is_error():
                marker = "X"
            elif event.event_id in evidence:
                marker = "!"
            elif event.type.value in {"tool_call", "decision"}:
                marker = ">"
            else:
                marker = "."

            label = event.type.value
            if event.name:
                label += f" ({event.name})"
            if event.agent_id:
                label += f" @{event.agent_id}"
            timeline.append(f"[{event.event_id:03d}] {marker} {label}")

        if len(self.trace.events) > max_events:
            timeline.append(f"... ({len(self.trace.events) - max_events} more events)")

        return timeline

    def _extract_root_causes(self) -> list[str]:
        """Extract root cause chain from analysis."""
        causes = []

        # From preanalysis hypotheses
        hypotheses = self.result.preanalysis.get("top_suspects", [])
        for hyp in hypotheses[:3]:  # Top 3
            causes.append(f"{hyp.get('hypothesis', 'Unknown')} (confidence: {hyp.get('confidence', 0):.0%})")

        return causes if causes else ["Root cause analysis incomplete"]

    def _extract_fixes(self) -> dict[str, list[str]]:
        """Extract fix recommendations categorized by type."""
        fixes = {
            "code": [],
            "tool": [],
            "prompt": [],
            "ops": [],
        }

        # From preanalysis
        hypotheses = self.result.preanalysis.get("top_suspects", [])
        for hyp in hypotheses:
            category = hyp.get("category", "code")
            suggested = hyp.get("suggested_fixes", [])
            if category in fixes:
                fixes[category].extend(suggested)
            else:
                fixes["code"].extend(suggested)

        # Add pattern-based deterministic templates
        pattern_templates = {
            "infinite_loop": ("code", "Add `max_iterations` guard and explicit terminal transition in router logic."),
            "retry_storm": ("ops", "Limit retries and apply exponential backoff with jitter for repeated failures."),
            "context_overflow": ("ops", "Summarize or trim long context before each LLM call to stay under limit."),
            "hallucinated_tool": ("prompt", "Constrain tool use to declared tool names and validate before dispatch."),
            "empty_response": ("tool", "Validate tool/LLM outputs and retry with bounded fallback on empty results."),
            "error_cascade": ("code", "Introduce localized error handling to stop one failure from propagating."),
            "goal_drift": ("prompt", "Re-anchor every few turns to the original goal and success criteria."),
            "stale_context": ("code", "Invalidate stale assumptions after each materially changed tool result."),
            "token_waste": ("ops", "Track token budget and stop low-value reasoning loops early."),
            "auth_permission_failure": ("ops", "Escalate repeated 401/403 failures and verify credential scopes."),
            "timeout_pattern": ("ops", "Set strict timeouts and fallback behavior for slow external dependencies."),
            "redundant_tool_call": ("code", "Memoize tool calls by normalized input to avoid duplicate work."),
        }
        for signal in self.result.preanalysis.get("signals", []):
            stype = signal.get("type")
            if stype in pattern_templates:
                category, recommendation = pattern_templates[stype]
                fixes[category].append(recommendation)

        # Deduplicate while preserving order
        for category in fixes:
            deduped: list[str] = []
            seen: set[str] = set()
            for fix in fixes[category]:
                if fix not in seen:
                    seen.add(fix)
                    deduped.append(fix)
            fixes[category] = deduped

        return fixes

    def _extract_confidence(self) -> float:
        """Extract confidence score from analysis."""
        hypotheses = self.result.preanalysis.get("top_suspects", [])
        if hypotheses:
            return hypotheses[0].get("confidence", 0.5)
        return 0.5

    def _extract_evidence_events(self) -> list[int]:
        """Extract event IDs cited as evidence."""
        events = set()

        # From preanalysis signals
        signals = self.result.preanalysis.get("signals", [])
        for signal in signals:
            events.update(signal.get("events", []))

        # From hypotheses
        hypotheses = self.result.preanalysis.get("top_suspects", [])
        for hyp in hypotheses:
            events.update(hyp.get("supporting_events", []))

        return sorted(list(events))

    def _calculate_health_score(self) -> int:
        """Compute 0-100 health score from deterministic signals.

        An event's failure evidence costs once: signals are applied
        heaviest-first and only their not-yet-claimed events earn the full
        weight, so one root cause reported by two detectors cannot stack.
        """
        score = 100
        severity_penalties = {
            "critical": 25,
            "high": 15,
            "medium": 8,
            "low": 3,
        }

        claimed: set[int] = set()
        signals = self.result.preanalysis.get("signals", [])
        ordered = sorted(
            signals,
            key=lambda s: severity_penalties.get(str(s.get("severity", "low")).lower(), 5),
            reverse=True,
        )
        for signal in ordered:
            severity = str(signal.get("severity", "low")).lower()
            weight = severity_penalties.get(severity, 5)
            events = [int(e) for e in signal.get("events", [])]
            if events:
                new_events = sum(1 for e in events if e not in claimed)
                penalty = weight * new_events / len(events)
                claimed.update(events)
            else:
                penalty = float(weight)
            score -= int(round(penalty))

        impacted_events = set(self._extract_evidence_events())
        total_events = max(1, len(self.trace.events))
        coverage_penalty = int((len(impacted_events) / total_events) * 20)
        score -= coverage_penalty

        return max(0, min(100, score))

    def to_markdown(self) -> str:
        """Generate markdown report."""
        report = self.generate()

        lines = [
            f"# Autopsy Report: Run {report.run_id}",
            "",
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## Summary",
            "",
        ]

        if "**Status:**" not in report.summary:
            lines.append(f"- **Status:** {report.status}")

        lines.extend([
            f"- **Health Score:** {report.health_score}/100",
            f"- **Confidence:** {report.confidence:.0%}",
            "",
            report.summary,
            "",
            "---",
            "",
            "## Timeline",
            "",
        ])

        for item in report.timeline:
            lines.append(f"- {item}")

        lines.extend([
            "",
            "---",
            "",
            "## Root Cause Chain",
            "",
        ])

        for i, cause in enumerate(report.root_cause_chain, 1):
            lines.append(f"{i}. {cause}")

        lines.extend([
            "",
            "---",
            "",
            "## Fix Recommendations",
            "",
        ])

        category_labels = {
            "code": "A) Graph/Code Fixes",
            "tool": "B) Tool Contract Fixes",
            "prompt": "C) Prompt/Policy Fixes",
            "ops": "D) Ops Fixes",
        }

        for category, label in category_labels.items():
            fixes = report.fix_recommendations.get(category, [])
            if fixes:
                lines.append(f"### {label}")
                lines.append("")
                for fix in fixes:
                    lines.append(f"- {fix}")
                lines.append("")

        lines.extend([
            "---",
            "",
            "## Evidence",
            "",
            f"**Cited Events:** {report.evidence_events}",
            "",
            "---",
            "",
            "## Trace Statistics",
            "",
        ])

        stats = report.trace_summary
        lines.extend([
            f"- Total Events: {stats.get('total_events', 'N/A')}",
            f"- LLM Calls: {stats.get('llm_calls', 'N/A')}",
            f"- Tool Calls: {stats.get('tool_calls', 'N/A')}",
            f"- Errors: {stats.get('errors', 'N/A')}",
            f"- Total Tokens: {stats.get('total_tokens', 'N/A')}",
            f"- Duration: {stats.get('duration_ms', 'N/A')} ms",
            "",
        ])

        # Full narrative: LLM synthesis and/or deterministic markdown from run_analysis_without_llm
        if report.raw_report:
            if "# Autopsy Report (deterministic)" in report.raw_report:
                # The synthesized sections above already cover the
                # deterministic doc's Summary and Hypotheses; only its
                # per-finding detail is unique.
                lines.extend(["---", "", *self._extract_findings_section(report.raw_report)])
            else:
                section_title = (
                    "## Deterministic analysis (no LLM)"
                    if "deterministic" in report.raw_report.lower()
                    else "## Detailed analysis"
                )
                lines.extend(["---", "", section_title, "", report.raw_report])

        return "\n".join(lines)

    @staticmethod
    def _extract_findings_section(narrative: str) -> list[str]:
        """Pull the Findings block out of the deterministic markdown."""
        lines = narrative.splitlines()
        try:
            start = next(i for i, line in enumerate(lines) if line.strip() == "## Findings")
        except StopIteration:
            return []
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        return [line for line in lines[start + 1 : end] if line.strip()]

    def to_json(self) -> dict[str, Any]:
        """Generate JSON report."""
        report = self.generate()

        return {
            "run_id": report.run_id,
            "status": report.status,
            "generated_at": report.generated_at.isoformat(),
            "summary": report.summary,
            "timeline": report.timeline,
            "root_cause_chain": report.root_cause_chain,
            "fix_recommendations": report.fix_recommendations,
            "confidence": report.confidence,
            "health_score": report.health_score,
            "evidence_events": report.evidence_events,
            "trace_summary": report.trace_summary,
            "preanalysis": report.preanalysis,
            # Markdown keeps the LLM narrative or deterministic findings
            # detail; JSON consumers get the same content under this key.
            "detailed_analysis": report.raw_report,
        }

    def render(self, format_name: str = "markdown") -> str | dict[str, Any]:
        """
        Render report using built-ins or plugin templates.

        Built-ins:
            - markdown
            - json
            - text (markdown with headings/bold stripped)
        """
        normalized = format_name.lower().strip()
        if normalized == "markdown":
            return self.to_markdown()
        if normalized == "json":
            return self.to_json()
        if normalized == "text":
            return markdown_to_plain(self.to_markdown())

        plugin_manager = get_plugin_manager()
        for plugin in plugin_manager.report_templates:
            if getattr(plugin, "format_name", "").lower() == normalized:
                return plugin.render(self.trace, self.result)

        raise ValueError(f"Unknown report format: {format_name}")

    def save(self, path: str | Path, format: str = "markdown") -> Path:
        """Save report to file."""
        path = Path(path)

        rendered = self.render(format)
        if isinstance(rendered, dict):
            import json

            content = json.dumps(rendered, indent=2, default=str)
            if not path.suffix:
                path = path.with_suffix(".json")
        else:
            content = str(rendered)
            if not path.suffix:
                if format == "markdown":
                    path = path.with_suffix(".md")
                elif format == "text":
                    path = path.with_suffix(".txt")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

        return path
