"""
Pattern detection module.

Detects common and advanced agent failure patterns in traces.
All detectors are deterministic and run before LLM analysis.
"""

import json
import logging
import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_autopsy.errors import PluginError
from agent_autopsy.plugins import get_plugin_manager
from agent_autopsy.schema import EventRole, EventType, Trace, TraceEvent, TraceStatus
from agent_autopsy.utils.config import get_config

logger = logging.getLogger(__name__)


class PatternType(str, Enum):
    """Types of detectable patterns."""

    INFINITE_LOOP = "infinite_loop"
    RETRY_STORM = "retry_storm"
    CONTEXT_OVERFLOW = "context_overflow"
    HALLUCINATED_TOOL = "hallucinated_tool"
    EMPTY_RESPONSE = "empty_response"
    ERROR_CASCADE = "error_cascade"
    GOAL_DRIFT = "goal_drift"
    STALE_CONTEXT = "stale_context"
    TOKEN_WASTE = "token_waste"
    AUTH_PERMISSION_FAILURE = "auth_permission_failure"
    TIMEOUT_PATTERN = "timeout_pattern"
    REDUNDANT_TOOL_CALL = "redundant_tool_call"
    INTER_AGENT_FAILURE = "inter_agent_failure"
    TOOL_CONTRACT_MISMATCH = "tool_contract_mismatch"


class Severity(str, Enum):
    """Severity level of detected patterns."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class PatternResult:
    """Result of pattern detection."""

    pattern_type: PatternType
    severity: Severity
    message: str
    evidence: str
    event_ids: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class PatternDetector:
    """Detects deterministic failure patterns in agent traces."""

    _AUTH_PERMISSION_RE = re.compile(
        r"(unauthori[sz]ed|forbidden|permission denied|access denied|invalid api key|\bauth(?:entication|orization)?\b)",
        flags=re.IGNORECASE,
    )
    _AUTH_STATUS_CODE_RE = re.compile(r"(?:^|[^\w-])(401|403)(?:$|[^\w-])")
    _TIMEOUT_RE = re.compile(
        r"(\btimeout\b|\btimed out\b|deadline exceeded|gateway timeout|request timeout)",
        flags=re.IGNORECASE,
    )

    def __init__(self, trace: Trace):
        self.trace = trace
        self._signature_cache: dict[int, str | None] = {}
        self._loops_cache: dict[int, list[PatternResult]] = {}

    def _signature(self, event: TraceEvent) -> str | None:
        """Memoized tool+input hash; detectors re-hash the same events."""
        eid = event.event_id
        if eid not in self._signature_cache:
            self._signature_cache[eid] = event.get_tool_signature()
        return self._signature_cache[eid]

    def detect_all(self) -> list[PatternResult]:
        """Run all pattern detectors and return results."""
        results: list[PatternResult] = []
        results.extend(self.detect_loops())
        results.extend(self.detect_retry_storms())
        results.extend(self.detect_redundant_tool_calls())
        results.extend(self.detect_empty_responses())
        results.extend(self.detect_error_cascades())
        results.extend(self.detect_hallucinated_tools())
        results.extend(self.detect_auth_permission_failures())
        results.extend(self.detect_timeout_patterns())
        results.extend(self.detect_goal_drift())
        results.extend(self.detect_stale_context())
        results.extend(self.detect_token_waste())
        results.extend(self.detect_inter_agent_failures())
        results.extend(self.detect_context_overflow())

        # Run community/plugin detectors.
        plugin_manager = get_plugin_manager()
        for plugin in plugin_manager.pattern_detectors:
            pname = getattr(plugin, "name", type(plugin).__name__)
            try:
                results.extend(plugin.detect(self.trace))
            except PluginError:
                raise
            except Exception:
                logger.exception("Pattern detector plugin %r failed; skipping plugin", pname)
                continue
        return results

    def _has_failure_evidence(self, events: list[TraceEvent]) -> bool:
        """True when the run is not a clean success (repeated-call failure gate).

        A repeated identical sequence is only a *failure pattern* when the
        underlying run is a failure; intentional idempotent repetitions in a
        healthy run must not be flagged.
        """
        if any(e.is_error() for e in events):
            return True
        if self.trace.status != TraceStatus.SUCCESS:
            return True
        return bool(self.trace.error_summary)

    def detect_loops(self, threshold: int | None = None) -> list[PatternResult]:
        """Detect infinite loops where the same tool+input is repeated consecutively."""
        config = get_config()
        threshold = threshold or config.loop_threshold
        if threshold in self._loops_cache:
            return self._loops_cache[threshold]

        results = []
        tool_calls = self.trace.get_tool_calls()

        if len(tool_calls) < threshold:
            self._loops_cache[threshold] = results
            return results

        consecutive_count = 1
        last_sig = None
        sequence_events: list[TraceEvent] = []

        for event in tool_calls:
            sig = self._signature(event)

            if sig == last_sig and sig is not None:
                consecutive_count += 1
                sequence_events.append(event)
            else:
                if consecutive_count >= threshold and last_sig and self._has_failure_evidence(sequence_events):
                    results.append(
                        PatternResult(
                            pattern_type=PatternType.INFINITE_LOOP,
                            severity=Severity.CRITICAL,
                            message=f"Identical tool call repeated {consecutive_count} times consecutively",
                            evidence=f"Same tool+input signature: {last_sig.split(':')[0]}",
                            event_ids=[e.event_id for e in sequence_events],
                            metadata={"signature": last_sig, "count": consecutive_count},
                        )
                    )

                consecutive_count = 1
                sequence_events = [event]

            last_sig = sig

        if consecutive_count >= threshold and last_sig and self._has_failure_evidence(sequence_events):
            results.append(
                PatternResult(
                    pattern_type=PatternType.INFINITE_LOOP,
                    severity=Severity.CRITICAL,
                    message=f"Identical tool call repeated {consecutive_count} times consecutively",
                    evidence=f"Same tool+input signature: {last_sig.split(':')[0]}",
                    event_ids=[e.event_id for e in sequence_events],
                    metadata={"signature": last_sig, "count": consecutive_count},
                )
            )

        self._loops_cache[threshold] = results
        return results

    def detect_retry_storms(self, threshold: int = 3) -> list[PatternResult]:
        """Detect retry storms where the same tool is called repeatedly within a time window."""
        results = []
        config = get_config()
        tool_calls = self.trace.get_tool_calls()

        if len(tool_calls) < threshold:
            return results

        window = timedelta(seconds=config.retry_window_seconds)

        tool_events: dict[str, list[TraceEvent]] = {}
        for event in tool_calls:
            if event.name:
                tool_events.setdefault(event.name, []).append(event)

        loop_ids = {eid for p in self.detect_loops() for eid in p.event_ids}

        for tool_name, events in tool_events.items():
            if len(events) < threshold:
                continue

            # Greedy clustering with a *chained* window: each candidate event
            # must fall within the window of the last event already in the
            # cluster (not of the cluster head). A chain of calls spaced just
            # inside the window would otherwise be split into sub-threshold
            # clusters and missed entirely. Maintained incrementally as a
            # two-pointer deque so rejected heads slide without rebuilding
            # the chain (each event enters and leaves the cluster once).
            n = len(events)

            def chains(candidate: TraceEvent, last: TraceEvent | None) -> bool:
                if last is None:
                    return True
                try:
                    if candidate.timestamp and last.timestamp:
                        delta = candidate.timestamp - last.timestamp
                        # Descending timestamps are exporter disorder, not
                        # retries; only forward motion may chain.
                        return timedelta(0) <= delta <= window
                    return candidate.event_id - last.event_id <= 10
                except TypeError:
                    # Mixed naive/aware timestamps: fall back to ID spacing.
                    return candidate.event_id - last.event_id <= 10

            head = 0
            tail = 0
            window_events: deque[TraceEvent] = deque()
            input_counts: Counter[str] = Counter()

            def extend_to_end() -> None:
                nonlocal tail
                while tail < n:
                    if not chains(events[tail], window_events[-1] if window_events else None):
                        break
                    window_events.append(events[tail])
                    input_counts[str(events[tail].input)] += 1
                    tail += 1

            while head < n:
                if not window_events:
                    window_events.append(events[head])
                    input_counts[str(events[head].input)] += 1
                    tail = max(tail, head + 1)
                extend_to_end()

                if len(window_events) >= threshold:
                    unique_inputs = len(input_counts)
                    cluster_ids = [e.event_id for e in window_events]
                    if (
                        unique_inputs <= len(window_events) // 2 + 1
                        and not any(eid in loop_ids for eid in cluster_ids)
                        and self._has_failure_evidence(list(window_events))
                    ):
                        results.append(
                            PatternResult(
                                pattern_type=PatternType.RETRY_STORM,
                                severity=Severity.HIGH,
                                message=f"Tool '{tool_name}' called {len(window_events)} times within {config.retry_window_seconds}s",
                                evidence=f"Multiple calls with similar inputs ({unique_inputs} unique inputs)",
                                event_ids=cluster_ids,
                                metadata={
                                    "tool_name": tool_name,
                                    "count": len(window_events),
                                    "unique_inputs": unique_inputs,
                                    "window_seconds": config.retry_window_seconds,
                                },
                            )
                        )
                        # Consume the whole reported run; nothing inside it
                        # can open a second storm.
                        head = tail
                        window_events.clear()
                        input_counts.clear()
                        continue

                evicted = events[head]
                window_events.popleft()
                input_counts[str(evicted.input)] -= 1
                if input_counts[str(evicted.input)] == 0:
                    del input_counts[str(evicted.input)]
                head += 1

        return results

    def detect_redundant_tool_calls(self) -> list[PatternResult]:
        """Detect repeated tool calls with identical inputs separated in time."""
        results = []
        calls_by_signature: dict[str, list[TraceEvent]] = {}

        for event in self.trace.get_tool_calls():
            sig = self._signature(event)
            if sig:
                calls_by_signature.setdefault(sig, []).append(event)

        for signature, events in calls_by_signature.items():
            if len(events) < 2:
                continue
            if not self._has_failure_evidence(events):
                continue

            event_ids = [e.event_id for e in events]
            non_consecutive_ids = [
                event_ids[i] for i in range(1, len(event_ids)) if event_ids[i] - event_ids[i - 1] > 1
            ]
            if non_consecutive_ids:
                tool_name = signature.split(":")[0]
                results.append(
                    PatternResult(
                        pattern_type=PatternType.REDUNDANT_TOOL_CALL,
                        severity=Severity.MEDIUM,
                        message=f"Tool '{tool_name}' called repeatedly with the same input at different points",
                        evidence="Identical tool signature appears in non-consecutive events",
                        event_ids=event_ids,
                        metadata={"signature": signature, "count": len(event_ids)},
                    )
                )

        return results

    def detect_empty_responses(self) -> list[PatternResult]:
        """Detect events with empty or null outputs."""
        results = []
        empty_events = []

        for event in self.trace.events:
            if event.type in [EventType.LLM_CALL, EventType.TOOL_CALL]:
                output = event.output
                is_empty = (
                    output is None
                    or output == ""
                    or (isinstance(output, str) and output.strip() == "")
                    or output == {}
                    or output == []
                )
                if not is_empty:
                    continue
                if event.type == EventType.TOOL_CALL:
                    # A successful tool returning no payload (e.g. a DELETE
                    # that answers `{}`) is legitimate. An errored call in an
                    # otherwise successful run is a recovered failure, not an
                    # empty model/tool response; failed runs still surface it.
                    recovered_without_summary = (
                        self.trace.status == TraceStatus.SUCCESS and not self.trace.error_summary
                    )
                    if not event.is_error() or recovered_without_summary:
                        continue
                empty_events.append(event.event_id)

        if empty_events:
            results.append(
                PatternResult(
                    pattern_type=PatternType.EMPTY_RESPONSE,
                    severity=Severity.MEDIUM,
                    message=f"Found {len(empty_events)} events with empty outputs",
                    evidence="Empty or null output detected",
                    event_ids=empty_events,
                )
            )

        return results

    def detect_error_cascades(self) -> list[PatternResult]:
        """Detect sequences of errors that propagate through the trace."""
        results = []
        error_events = self.trace.get_error_events()

        if len(error_events) < 2:
            return results

        error_ids = [e.event_id for e in error_events]
        cascades: list[list[int]] = []
        current_cascade = [error_ids[0]]

        for i in range(1, len(error_ids)):
            if error_ids[i] - error_ids[i - 1] <= 3:
                current_cascade.append(error_ids[i])
            else:
                if len(current_cascade) >= 2:
                    cascades.append(current_cascade)
                current_cascade = [error_ids[i]]

        if len(current_cascade) >= 2:
            cascades.append(current_cascade)

        for cascade in cascades:
            results.append(
                PatternResult(
                    pattern_type=PatternType.ERROR_CASCADE,
                    severity=Severity.HIGH,
                    message=f"Error cascade: {len(cascade)} consecutive errors",
                    evidence="Errors propagating sequentially across events",
                    event_ids=cascade,
                    metadata={"cascade_length": len(cascade)},
                )
            )

        return results

    def detect_hallucinated_tools(self) -> list[PatternResult]:
        """Detect tool calls to tools not in the available tools list."""
        results = []
        available_tools = set(self.trace.env.tools_available)

        if not available_tools:
            return results

        hallucinated = []
        for event in self.trace.get_tool_calls():
            if event.name and event.name not in available_tools:
                hallucinated.append(event.event_id)

        if hallucinated:
            results.append(
                PatternResult(
                    pattern_type=PatternType.HALLUCINATED_TOOL,
                    severity=Severity.HIGH,
                    message=f"Found {len(hallucinated)} calls to unknown tools",
                    evidence=f"Tool called not in available tools: {available_tools}",
                    event_ids=hallucinated,
                    metadata={"available_tools": list(available_tools)},
                )
            )

        return results

    def detect_auth_permission_failures(self) -> list[PatternResult]:
        """Detect repeated auth/permission failures that should trigger escalation."""
        matched_events: list[TraceEvent] = []
        for event in self.trace.events:
            text_parts = []
            if event.error and event.error.message:
                text_parts.append(event.error.message)
            if event.output is not None:
                text_parts.append(str(event.output))
            if event.input is not None:
                text_parts.append(str(event.input))
            combined = " ".join(text_parts)
            if self._AUTH_PERMISSION_RE.search(combined) or self._AUTH_STATUS_CODE_RE.search(combined):
                matched_events.append(event)

        # Auth language inside prompt/output prose on an otherwise healthy
        # run is discussion, not failure evidence.
        if len(matched_events) >= 2 and self._has_failure_evidence(matched_events):
            return [
                PatternResult(
                    pattern_type=PatternType.AUTH_PERMISSION_FAILURE,
                    severity=Severity.HIGH,
                    message=f"Detected repeated authentication/permission failures ({len(matched_events)} events)",
                    evidence="Auth/permission-related error signatures were repeated",
                    event_ids=[e.event_id for e in matched_events],
                )
            ]
        return []

    def detect_timeout_patterns(self) -> list[PatternResult]:
        """Detect timeout-driven failures and slow-call bottlenecks."""
        config = get_config()
        timeout_ids: list[int] = []
        slow_call_ids: list[int] = []

        trace_failed = self.trace.status != TraceStatus.SUCCESS

        for event in self.trace.events:
            text_parts = []
            if event.error and event.error.message:
                text_parts.append(event.error.message)
            if event.output is not None:
                text_parts.append(str(event.output))
            text_hit = bool(self._TIMEOUT_RE.search(" ".join(text_parts)))

            # A slow call is only a timeout signal when the event errored or
            # the run itself failed; a healthy slow call is not a timeout.
            if (
                event.latency_ms
                and event.latency_ms >= config.timeout_seconds * 1000
                and (event.is_error() or trace_failed)
            ):
                slow_call_ids.append(event.event_id)
            # Timeout language counts on an errored event, or in assistant/tool
            # output when the run itself failed. User/system prompts that only
            # discuss timeout handling remain negative controls.
            failed_output_evidence = trace_failed and (
                event.type in {EventType.LLM_CALL, EventType.TOOL_CALL, EventType.ERROR}
                or event.role in {EventRole.ASSISTANT, EventRole.TOOL}
            )
            if text_hit and (event.is_error() or failed_output_evidence):
                timeout_ids.append(event.event_id)

        all_ids = sorted(set(timeout_ids + slow_call_ids))
        if len(all_ids) >= 1:
            return [
                PatternResult(
                    pattern_type=PatternType.TIMEOUT_PATTERN,
                    severity=Severity.HIGH,
                    message=f"Detected timeout-related behavior across {len(all_ids)} events",
                    evidence="Timeout errors and/or slow external calls indicate latency bottlenecks",
                    event_ids=all_ids,
                    metadata={
                        "timeout_error_events": timeout_ids,
                        "slow_call_events": slow_call_ids,
                    },
                )
            ]
        return []

    def detect_goal_drift(self) -> list[PatternResult]:
        """Detect semantic drift away from the original goal."""
        if not self.trace.task or not self.trace.task.goal:
            return []

        scored_events = self._semantic_similarity_series(self.trace.task.goal)
        if not scored_events:
            return []

        if len(scored_events) < 6:
            return []

        config = get_config()
        window = max(3, len(scored_events) // 3)
        early_scores = [s for _, s in scored_events[:window]]
        late_scores = [s for _, s in scored_events[-window:]]
        early_avg = sum(early_scores) / len(early_scores)
        late_avg = sum(late_scores) / len(late_scores)

        if (
            early_avg - late_avg >= config.semantic_drift_delta_threshold
            and late_avg <= config.semantic_drift_low_threshold
        ):
            late_ids = [eid for eid, _ in scored_events[-window:]]
            return [
                PatternResult(
                    pattern_type=PatternType.GOAL_DRIFT,
                    severity=Severity.MEDIUM,
                    message="Agent behavior drifted away from the initial goal",
                    evidence=f"Goal similarity dropped from {early_avg:.2f} to {late_avg:.2f}",
                    event_ids=late_ids,
                    metadata={
                        "method": (
                            "semantic_embeddings"
                            if self._embedding_backend_available(self.trace.task.goal)
                            else "lexical_overlap"
                        ),
                        "early_similarity": round(early_avg, 3),
                        "late_similarity": round(late_avg, 3),
                        "series": [
                            {"event_id": event_id, "similarity": round(score, 3)} for event_id, score in scored_events
                        ],
                    },
                )
            ]
        return []

    def detect_stale_context(self) -> list[PatternResult]:
        """Detect when repeated calls reuse old context despite changed tool outputs."""
        results: list[PatternResult] = []
        signatures: dict[str, list[TraceEvent]] = {}
        for event in self.trace.get_tool_calls():
            sig = self._signature(event)
            if sig:
                signatures.setdefault(sig, []).append(event)

        for sig, events in signatures.items():
            if len(events) < 3:
                continue
            outputs = [str(e.output) for e in events if e.output is not None]
            if len(set(outputs)) > 1:
                results.append(
                    PatternResult(
                        pattern_type=PatternType.STALE_CONTEXT,
                        severity=Severity.MEDIUM,
                        message="Repeated tool calls used stale context after outputs changed",
                        evidence="Same tool+input signature produced different outputs across retries",
                        event_ids=[e.event_id for e in events],
                        metadata={"signature": sig, "unique_outputs": len(set(outputs))},
                    )
                )
        return results

    def detect_token_waste(self) -> list[PatternResult]:
        """Detect when token spend is high relative to useful state transitions."""
        llm_events = [e for e in self.trace.get_llm_calls() if e.token_count]
        total_llm_tokens = sum(e.token_count or 0 for e in llm_events)
        if total_llm_tokens < 1500:
            return []

        # Neighbor lookup via id map: get_events_in_range scans every event
        # per LLM call, which is quadratic on long traces.
        by_id = {e.event_id: e for e in self.trace.events}
        useful_tokens = 0
        for event in llm_events:
            nearby = [
                by_id[i]
                for i in range(max(0, event.event_id - 2), event.event_id + 3)
                if i != event.event_id and i in by_id
            ]
            has_useful_transition = any(
                e.type in [EventType.TOOL_CALL, EventType.ERROR, EventType.DECISION]
                for e in nearby
                if e.event_id != event.event_id
            )
            if has_useful_transition:
                useful_tokens += event.token_count or 0

        waste_tokens = total_llm_tokens - useful_tokens
        waste_ratio = waste_tokens / total_llm_tokens if total_llm_tokens else 0

        if waste_ratio >= 0.6:
            top_events = sorted(llm_events, key=lambda e: e.token_count or 0, reverse=True)[:5]
            return [
                PatternResult(
                    pattern_type=PatternType.TOKEN_WASTE,
                    severity=Severity.MEDIUM,
                    message=f"High token waste detected ({waste_ratio:.0%} of LLM tokens)",
                    evidence=f"Useful-token ratio is low ({(1 - waste_ratio):.0%})",
                    event_ids=[e.event_id for e in top_events],
                    metadata={
                        "total_llm_tokens": total_llm_tokens,
                        "useful_llm_tokens": useful_tokens,
                        "waste_tokens": waste_tokens,
                        "waste_ratio": round(waste_ratio, 3),
                    },
                )
            ]
        return []

    def detect_inter_agent_failures(self) -> list[PatternResult]:
        """Detect cascades where failures propagate between different agents."""
        if len(self.trace.get_agent_ids()) < 2:
            return []

        results: list[PatternResult] = []
        for i, event in enumerate(self.trace.events[:-1]):
            if not event.is_error() or not event.agent_id:
                continue
            for next_event in self.trace.events[i + 1 : i + 4]:
                if not next_event.agent_id or next_event.agent_id == event.agent_id:
                    continue
                if next_event.is_error():
                    results.append(
                        PatternResult(
                            pattern_type=PatternType.INTER_AGENT_FAILURE,
                            severity=Severity.HIGH,
                            message="Failure appears to propagate across agent handoff",
                            evidence=(
                                f"Agent '{event.agent_id}' errored, followed by agent "
                                f"'{next_event.agent_id}' error shortly after"
                            ),
                            event_ids=[event.event_id, next_event.event_id],
                            metadata={
                                "from_agent": event.agent_id,
                                "to_agent": next_event.agent_id,
                            },
                        )
                    )
                    break
        return results

    def detect_context_overflow(self, threshold: int | None = None) -> list[PatternResult]:
        """
        Detect potential context overflow based on token counts.

        Limit source precedence:
        1) explicit threshold argument
        2) per-trace context window override
        3) model-specific context limits from config file
        4) global config threshold
        """
        results = []
        config = get_config()
        total_tokens = self.trace.stats.total_tokens or 0

        model = self.trace.env.model
        trace_context_limit = self.trace.env.context_window_tokens
        model_limit = self._get_model_context_limit(model, config.model_context_limits_path)
        metadata: dict[str, object] = {
            "config_threshold": config.context_overflow_threshold,
            "model": model,
        }

        if threshold is not None:
            limit = threshold
            metadata["active_limit_source"] = "explicit_threshold"
        elif trace_context_limit:
            limit = trace_context_limit
            metadata["trace_context_window_tokens"] = trace_context_limit
            metadata["active_limit_source"] = "trace_context_window_tokens"
        elif model_limit:
            limit = model_limit
            metadata["model_context_limit"] = model_limit
            metadata["active_limit_source"] = "model_context_limits_config"
        else:
            limit = config.context_overflow_threshold
            metadata["active_limit_source"] = "config_context_overflow_threshold"

        if total_tokens >= limit:
            token_events = [
                (e.event_id, e.token_count) for e in self.trace.events if e.token_count and e.token_count > 0
            ]
            token_events.sort(key=lambda x: x[1], reverse=True)
            top_events = [e[0] for e in token_events[:5]]

            results.append(
                PatternResult(
                    pattern_type=PatternType.CONTEXT_OVERFLOW,
                    severity=Severity.CRITICAL,
                    message=f"Token count ({total_tokens}) approaching/exceeding context limit",
                    evidence=f"Total tokens: {total_tokens}, threshold: {limit}"
                    + (f" (model: {model})" if model else ""),
                    event_ids=top_events,
                    metadata={
                        "total_tokens": total_tokens,
                        "threshold": limit,
                        **metadata,
                    },
                )
            )

        return results

    def _event_text(self, event: TraceEvent) -> str:
        """Build a compact semantic representation for an event."""
        parts = [event.name or ""]
        if event.input is not None:
            parts.append(str(event.input))
        if event.output is not None:
            parts.append(str(event.output))
        if event.agent_id:
            parts.append(event.agent_id)
        return " ".join(parts)

    def _tokenize_text(self, text: str) -> list[str]:
        """Lightweight tokenizer for lexical overlap heuristics."""
        return re.findall(r"[a-z0-9_]{3,}", text.lower())

    def _semantic_similarity_series(self, goal: str) -> list[tuple[int, float]]:
        """
        Build event->similarity series using embeddings when available,
        otherwise lexical overlap fallback.
        """
        content_events = [
            event
            for event in self.trace.events
            if event.type in [EventType.LLM_CALL, EventType.TOOL_CALL, EventType.DECISION]
        ]
        event_texts = [self._event_text(event) for event in content_events]

        if self._should_use_sentence_embeddings(goal) and self._embedding_backend_available(goal):
            try:
                model = self._get_embedding_model(get_config().semantic_drift_model)
                embeddings = model.encode([goal] + event_texts)
                goal_vec = embeddings[0]
                series: list[tuple[int, float]] = []
                for event, vec in zip(content_events, embeddings[1:]):
                    similarity = self._cosine_similarity(goal_vec, vec)
                    series.append((event.event_id, similarity))
                if series:
                    return series
            except Exception:
                logger.exception("Sentence embedding similarity failed; falling back to lexical overlap for goal drift")

        # Lexical fallback.
        goal_tokens = set(self._tokenize_text(goal))
        if not goal_tokens:
            return []
        series = []
        for event, text in zip(content_events, event_texts):
            tokens = set(self._tokenize_text(text))
            if not tokens:
                continue
            overlap = len(tokens & goal_tokens) / len(goal_tokens)
            series.append((event.event_id, overlap))
        return series

    def _should_use_sentence_embeddings(self, goal: str) -> bool:
        """Load embeddings only when drift is enabled, goal exists, and user did not opt out."""
        cfg = get_config()
        if getattr(cfg, "skip_embeddings", False):
            return False
        if not cfg.semantic_drift_enabled:
            return False
        return bool(goal and goal.strip())

    def _embedding_backend_available(self, goal: str) -> bool:
        """Return True when sentence-transformers is importable (no model load)."""
        if not self._should_use_sentence_embeddings(goal):
            return False
        try:
            import sentence_transformers  # noqa: F401

            return True
        except ImportError:
            return False
        except Exception:
            logger.exception("Unexpected error while probing sentence-transformers backend")
            return False

    @classmethod
    @lru_cache(maxsize=2)
    def _get_embedding_model(cls, model_name: str):
        """Lazily load sentence-transformers model."""
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(model_name)

    @staticmethod
    def _cosine_similarity(a, b) -> float:
        """Compute cosine similarity for vector-like iterables."""
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
        norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @classmethod
    def _get_model_context_limit(cls, model: str | None, configured_path: str = "") -> int | None:
        """Get model context limit from JSON config with partial model-name matching."""
        if not model:
            return None

        limits = cls._load_model_context_limits(configured_path)
        if not limits:
            return None

        model_lower = model.lower()
        for model_name in sorted(limits.keys(), key=len, reverse=True):
            if model_name.lower() in model_lower:
                return limits[model_name]
        return None

    @classmethod
    @lru_cache(maxsize=4)
    def _load_model_context_limits(cls, configured_path: str = "") -> dict[str, int]:
        """Load model limits from configured path or bundled defaults."""
        paths: list[Path] = []
        if configured_path:
            paths.append(Path(configured_path))
        paths.append(Path(__file__).with_name("model_context_limits.json"))

        for path in paths:
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text())
                parsed = {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float, str))}
                if parsed:
                    return parsed
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return {}

    def find_errors(self) -> list[TraceEvent]:
        """Get all error events from the trace."""
        return self.trace.get_error_events()

    def find_loops(self) -> list[PatternResult]:
        """Alias for detect_loops for tool compatibility."""
        return self.detect_loops()
