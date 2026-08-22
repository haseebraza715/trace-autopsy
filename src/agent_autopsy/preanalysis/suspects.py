"""
Root cause candidate builder.

Aggregates signals from pattern detection and contract validation
to generate root cause hypotheses with confidence scores.
"""

from dataclasses import dataclass, field
from typing import Any

from agent_autopsy.schema import Trace

from .contracts import ContractValidator, ContractViolation
from .patterns import PatternDetector, PatternResult


@dataclass
class Signal:
    """A detected signal that may indicate a root cause."""
    type: str
    severity: str
    evidence: str
    event_ids: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hypothesis:
    """A root cause hypothesis with confidence score."""
    description: str
    confidence: float  # 0.0 to 1.0
    supporting_events: list[int] = field(default_factory=list)
    category: str = "unknown"  # code, prompt, tool, ops
    suggested_fixes: list[str] = field(default_factory=list)


@dataclass
class PreAnalysisBundle:
    """Complete pre-analysis output for LLM consumption."""
    signals: list[Signal] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "signals": [
                {
                    "type": s.type,
                    "severity": s.severity,
                    "evidence": s.evidence,
                    "events": s.event_ids,
                }
                for s in self.signals
            ],
            "top_suspects": [
                {
                    "hypothesis": h.description,
                    "confidence": h.confidence,
                    "supporting_events": h.supporting_events,
                    "category": h.category,
                    "suggested_fixes": h.suggested_fixes,
                }
                for h in self.hypotheses
            ],
            "summary": self.summary,
        }


class RootCauseBuilder:
    """
    Builds root cause hypotheses from detected patterns and violations.

    Combines signals into actionable hypotheses with confidence scores.
    """

    def __init__(self, trace: Trace):
        self.trace = trace
        self.pattern_detector = PatternDetector(trace)
        self.contract_validator = ContractValidator(trace)

    def build(self) -> PreAnalysisBundle:
        """Build the complete pre-analysis bundle."""
        # Collect signals from patterns
        patterns = self.pattern_detector.detect_all()
        signals = self._patterns_to_signals(patterns)

        # Collect signals from contract violations
        violations = self.contract_validator.get_violations()
        signals.extend(self._violations_to_signals(violations))

        # Generate hypotheses from signals
        hypotheses = self._generate_hypotheses(signals, patterns, violations)

        # Sort hypotheses by confidence
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        # Build summary
        summary = self._build_summary(signals, hypotheses)

        return PreAnalysisBundle(
            signals=signals,
            hypotheses=hypotheses,
            summary=summary,
        )

    def _patterns_to_signals(self, patterns: list[PatternResult]) -> list[Signal]:
        """Convert pattern results to signals."""
        signals: list[Signal] = []

        for pattern in patterns:
            signals.append(
                Signal(
                    type=pattern.pattern_type.value,
                    severity=pattern.severity.value,
                    evidence=pattern.evidence,
                    event_ids=pattern.event_ids,
                    metadata=pattern.metadata,
                )
            )

        return signals

    def _violations_to_signals(self, violations: list[ContractViolation]) -> list[Signal]:
        """Convert contract violations to signals."""
        signals: list[Signal] = []

        for violation in violations:
            signals.append(
                Signal(
                    type=f"contract_{violation.violation_type}",
                    severity=violation.severity.value,
                    evidence=violation.message,
                    event_ids=[violation.event_id],
                    metadata={"tool": violation.tool_name, "fix": violation.suggested_fix},
                )
            )

        return signals

    def _generate_hypotheses(
        self,
        signals: list[Signal],
        patterns: list[PatternResult],
        violations: list[ContractViolation],
    ) -> list[Hypothesis]:
        """Generate root cause hypotheses from signals."""
        hypotheses: list[Hypothesis] = []

        # Hypothesis: Loop due to missing exit condition
        loop_signals = [s for s in signals if "loop" in s.type.lower()]
        if loop_signals:
            all_events = []
            for s in loop_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Missing exit condition in graph/router logic",
                    confidence=0.85 if len(loop_signals) > 0 else 0.0,
                    supporting_events=sorted(set(all_events)),
                    category="code",
                    suggested_fixes=[
                        "Add max iteration limit to graph execution",
                        "Add exit condition check in router node",
                        "Implement loop detection with early termination",
                    ],
                )
            )

        # Hypothesis: Retry policy issues
        retry_signals = [s for s in signals if "retry" in s.type.lower() or "storm" in s.type.lower()]
        if retry_signals:
            all_events = []
            for s in retry_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Missing or misconfigured retry policy",
                    confidence=0.75,
                    supporting_events=sorted(set(all_events)),
                    category="ops",
                    suggested_fixes=[
                        "Add exponential backoff to tool calls",
                        "Set maximum retry count",
                        "Implement circuit breaker pattern",
                    ],
                )
            )

        # Hypothesis: Hallucinated tool
        hallucination_signals = [s for s in signals if "hallucin" in s.type.lower()]
        if hallucination_signals:
            all_events = []
            for s in hallucination_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Model calling non-existent tools (hallucination)",
                    confidence=0.90,
                    supporting_events=sorted(set(all_events)),
                    category="prompt",
                    suggested_fixes=[
                        "Add stricter tool definitions in system prompt",
                        "Validate tool names before execution",
                        "Use structured output for tool selection",
                    ],
                )
            )

        # Hypothesis: Error cascade from unhandled exception
        cascade_signals = [s for s in signals if "cascade" in s.type.lower()]
        if cascade_signals:
            all_events = []
            for s in cascade_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Unhandled error causing cascade failures",
                    confidence=0.80,
                    supporting_events=sorted(set(all_events)),
                    category="code",
                    suggested_fixes=[
                        "Add try/except blocks around tool calls",
                        "Implement graceful error recovery",
                        "Add fallback behavior for failed operations",
                    ],
                )
            )

        # Hypothesis: Context overflow
        overflow_signals = [s for s in signals if "overflow" in s.type.lower()]
        if overflow_signals:
            all_events = []
            for s in overflow_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Context window overflow causing truncation or failure",
                    confidence=0.85,
                    supporting_events=sorted(set(all_events)),
                    category="ops",
                    suggested_fixes=[
                        "Implement context summarization",
                        "Use sliding window for conversation history",
                        "Switch to model with larger context",
                    ],
                )
            )

        # Hypothesis: Empty response
        empty_signals = [s for s in signals if "empty" in s.type.lower()]
        if empty_signals:
            all_events = []
            for s in empty_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Tool or model returning empty/null responses",
                    confidence=0.65,
                    supporting_events=sorted(set(all_events)),
                    category="tool",
                    suggested_fixes=[
                        "Add output validation on tool results",
                        "Handle null responses gracefully",
                        "Add retry logic for empty responses",
                    ],
                )
            )

        # Hypothesis: Goal drift
        goal_drift_signals = [s for s in signals if "goal_drift" in s.type.lower()]
        if goal_drift_signals:
            all_events = []
            for s in goal_drift_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Agent drifted away from the original objective",
                    confidence=0.70,
                    supporting_events=sorted(set(all_events)),
                    category="prompt",
                    suggested_fixes=[
                        "Reinforce task objective and success criteria in system prompt",
                        "Add periodic goal-check checkpoints in execution flow",
                        "Terminate or escalate when progress diverges from goal",
                    ],
                )
            )

        # Hypothesis: Stale context use
        stale_context_signals = [s for s in signals if "stale_context" in s.type.lower()]
        if stale_context_signals:
            all_events = []
            for s in stale_context_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Agent reused stale context instead of adapting to new tool outputs",
                    confidence=0.72,
                    supporting_events=sorted(set(all_events)),
                    category="code",
                    suggested_fixes=[
                        "Invalidate cached assumptions when tool outputs change",
                        "Recompute plan after each materially different tool result",
                        "Compare current state against latest tool output before retries",
                    ],
                )
            )

        # Hypothesis: Token waste
        token_waste_signals = [s for s in signals if "token_waste" in s.type.lower()]
        if token_waste_signals:
            all_events = []
            for s in token_waste_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Excessive token spend on low-value reasoning turns",
                    confidence=0.68,
                    supporting_events=sorted(set(all_events)),
                    category="ops",
                    suggested_fixes=[
                        "Add token budget and early-stop criteria",
                        "Trim verbose intermediate messages and repeated context",
                        "Use concise tool outputs with structured summaries",
                    ],
                )
            )

        # Hypothesis: Auth or permission failures
        auth_signals = [s for s in signals if "auth_permission_failure" in s.type.lower()]
        if auth_signals:
            all_events = []
            for s in auth_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Authentication or permission issues blocked tool execution",
                    confidence=0.85,
                    supporting_events=sorted(set(all_events)),
                    category="ops",
                    suggested_fixes=[
                        "Validate credentials and scopes before execution",
                        "Escalate after repeated 401/403 failures instead of retrying",
                        "Add explicit auth error handling with user-facing guidance",
                    ],
                )
            )

        # Hypothesis: Timeout bottleneck
        timeout_signals = [s for s in signals if "timeout_pattern" in s.type.lower()]
        if timeout_signals:
            all_events = []
            for s in timeout_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="External dependency latency caused timeout-driven failures",
                    confidence=0.78,
                    supporting_events=sorted(set(all_events)),
                    category="ops",
                    suggested_fixes=[
                        "Set per-tool timeout and retry budgets",
                        "Introduce fallback providers for slow dependencies",
                        "Cache expensive calls and short-circuit known slow paths",
                    ],
                )
            )

        # Hypothesis: Redundant tool calls
        redundant_signals = [s for s in signals if "redundant_tool_call" in s.type.lower()]
        if redundant_signals:
            all_events = []
            for s in redundant_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Repeated tool requests indicate missing memory or memoization",
                    confidence=0.67,
                    supporting_events=sorted(set(all_events)),
                    category="code",
                    suggested_fixes=[
                        "Memoize tool outputs by tool name and normalized input",
                        "Check prior tool results before dispatching a duplicate call",
                        "Store and reuse resolved answers within the agent state",
                    ],
                )
            )

        # Hypothesis: Multi-agent handoff failure
        inter_agent_signals = [s for s in signals if "inter_agent_failure" in s.type.lower()]
        if inter_agent_signals:
            all_events = []
            for s in inter_agent_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Inter-agent handoff propagated invalid state or errors",
                    confidence=0.74,
                    supporting_events=sorted(set(all_events)),
                    category="code",
                    suggested_fixes=[
                        "Add schema validation at agent handoff boundaries",
                        "Include explicit handoff contracts for cross-agent messages",
                        "Isolate failures so downstream agents can degrade gracefully",
                    ],
                )
            )

        # Hypothesis: Contract violations
        contract_signals = [s for s in signals if "contract" in s.type.lower()]
        if contract_signals:
            all_events = []
            for s in contract_signals:
                all_events.extend(s.event_ids)

            hypotheses.append(
                Hypothesis(
                    description="Tool input/output not matching expected schema",
                    confidence=0.70,
                    supporting_events=sorted(set(all_events)),
                    category="tool",
                    suggested_fixes=[
                        "Add schema validation before tool calls",
                        "Update tool schemas to match actual behavior",
                        "Add type coercion for common mismatches",
                    ],
                )
            )

        # If no specific hypotheses, add generic ones based on trace status
        if not hypotheses and self.trace.error_summary:
            hypotheses.append(
                Hypothesis(
                    description=f"Execution failed: {self.trace.error_summary}",
                    confidence=0.50,
                    supporting_events=[e.event_id for e in self.trace.get_error_events()],
                    category="unknown",
                    suggested_fixes=[
                        "Review error messages for specific causes",
                        "Add error handling around failure points",
                    ],
                )
            )

        return hypotheses

    def _build_summary(self, signals: list[Signal], hypotheses: list[Hypothesis]) -> str:
        """Build a human-readable summary."""
        if not signals:
            return "No significant issues detected in trace."

        parts = []

        # Count by severity
        critical = sum(1 for s in signals if s.severity == "critical")
        high = sum(1 for s in signals if s.severity == "high")
        medium = sum(1 for s in signals if s.severity == "medium")
        low = sum(1 for s in signals if s.severity == "low")

        if critical > 0:
            parts.append(f"{critical} critical issue(s)")
        if high > 0:
            parts.append(f"{high} high severity issue(s)")
        if medium > 0:
            parts.append(f"{medium} medium severity issue(s)")
        if low > 0:
            parts.append(f"{low} low severity issue(s)")

        summary = f"Found {', '.join(parts)}. "

        if hypotheses:
            top = hypotheses[0]
            summary += f"Top hypothesis: {top.description} (confidence: {top.confidence:.0%})"

        return summary
