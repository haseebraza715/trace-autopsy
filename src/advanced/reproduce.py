"""
Re-execute LLM calls from a captured trace and check whether the same failure
patterns recur.

This bridges debugging into regression testing: after applying a fix, you can
ask "does this trace's failure still reproduce?" without re-driving the entire
agent. Only LLM calls are replayed — tool calls and decisions are not
re-executed because re-driving an agent end-to-end is out of scope.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from src.preanalysis import PatternDetector
from src.schema import EventType, Trace, TraceEvent
from src.utils.config import Config, get_config

logger = logging.getLogger(__name__)


@dataclass
class LLMReplayOutcome:
    """Result of replaying one LLM event one or more times."""

    event_id: int
    model: str | None
    original_output: Any
    new_outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ReproductionResult:
    """Aggregate result of a reproduction run."""

    runs: int  # how many times each event was replayed
    per_event: list[LLMReplayOutcome] = field(default_factory=list)
    # Pattern types that fired on the original trace AND still fired on every
    # synthetic re-run. These are the ones whose root cause is *not* in the LLM
    # output (the failure persists even with fresh generations).
    persistent_patterns: set[str] = field(default_factory=set)
    # Pattern types that fired on the original but didn't fire on any re-run.
    cleared_patterns: set[str] = field(default_factory=set)
    # How many of `runs` reproduced *at least one* of the original patterns.
    reproduced_count: int = 0

    @property
    def reproduce_rate(self) -> float:
        return self.reproduced_count / self.runs if self.runs else 0.0

    def summary_line(self) -> str:
        return (
            f"reproduces {self.reproduced_count}/{self.runs} times "
            f"({self.reproduce_rate:.0%}); "
            f"persistent={sorted(self.persistent_patterns) or 'none'}, "
            f"cleared={sorted(self.cleared_patterns) or 'none'}"
        )


# An LLMInvoker takes (model_name, prompt) and returns the new output text.
LLMInvoker = Callable[[str, str], str]


def _default_llm_invoker(cfg: Config | None = None) -> LLMInvoker:
    """
    Build an invoker using the same provider abstraction as the analysis agent.

    Lazy-imports langchain so callers without it can still construct a
    Reproducer with a custom invoker (e.g. for tests).
    """
    cfg = cfg or get_config()
    provider = (cfg.llm_provider or "openrouter").lower().strip()

    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise RuntimeError(
            "langchain is required for live reproduction. Install with "
            "`pip install -e '.[llm]'` or supply a custom invoker."
        ) from exc

    def _invoke(model: str, prompt: str) -> str:
        if provider == "openrouter":
            llm = init_chat_model(
                f"openai:{model}",
                model_provider="openai",
                api_key=cfg.openrouter_api_key or None,
                base_url=cfg.openrouter_base_url,
                temperature=0.1,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout_seconds,
            )
        elif provider == "openai":
            llm = init_chat_model(
                f"openai:{model}",
                model_provider="openai",
                api_key=cfg.openai_api_key or os.getenv("OPENAI_API_KEY") or None,
                base_url=cfg.openai_api_base or os.getenv("OPENAI_API_BASE") or None,
                temperature=0.1,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout_seconds,
            )
        elif provider == "anthropic":
            llm = init_chat_model(
                f"anthropic:{model}",
                api_key=os.getenv("ANTHROPIC_API_KEY") or None,
                temperature=0.1,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout_seconds,
            )
        elif provider == "ollama":
            llm = init_chat_model(
                f"ollama:{model}",
                base_url=cfg.ollama_base_url,
                temperature=0.1,
            )
        else:
            raise RuntimeError(f"Unsupported provider for reproduce: {provider}")
        result = llm.invoke(prompt)
        # langchain returns a Message; normalize to plain text
        return getattr(result, "content", str(result))

    return _invoke


class Reproducer:
    """Replay LLM calls from a trace and compare against the original outcome."""

    def __init__(
        self,
        trace: Trace,
        *,
        invoker: LLMInvoker | None = None,
        model_override: str | None = None,
    ):
        self.trace = trace
        self.invoker = invoker or _default_llm_invoker()
        self.model_override = model_override

    def _llm_events(self) -> list[TraceEvent]:
        return [e for e in self.trace.events if e.type == EventType.LLM_CALL]

    def _prompt_for(self, event: TraceEvent) -> str:
        """Best-effort: convert event input into a prompt string."""
        if isinstance(event.input, str):
            return event.input
        if isinstance(event.input, (dict, list)):
            import json
            return json.dumps(event.input, default=str)
        return str(event.input or "")

    def _model_for(self, event: TraceEvent) -> str | None:
        return self.model_override or event.name or (self.trace.env.model if self.trace.env else None)

    def _synthetic_trace_with_outputs(
        self,
        new_outputs: dict[int, str],
    ) -> Trace:
        """Clone the trace, replacing LLM event outputs with the new strings."""
        cloned_events: list[TraceEvent] = []
        for ev in self.trace.events:
            if ev.event_id in new_outputs:
                cloned_events.append(ev.model_copy(update={"output": new_outputs[ev.event_id]}))
            else:
                cloned_events.append(ev)
        return self.trace.model_copy(update={"events": cloned_events})

    def reproduce(self, *, n: int = 1) -> ReproductionResult:
        """
        Re-issue every LLM call in the trace `n` times and report whether the
        original failure patterns recur in the synthetic trace.
        """
        original_patterns = {
            p.pattern_type.value for p in PatternDetector(self.trace).detect_all()
        }
        result = ReproductionResult(runs=n)
        per_event: dict[int, LLMReplayOutcome] = {}
        for ev in self._llm_events():
            per_event[ev.event_id] = LLMReplayOutcome(
                event_id=ev.event_id,
                model=self._model_for(ev),
                original_output=ev.output,
            )

        per_run_persistent: list[set[str]] = []
        for _ in range(n):
            new_outputs: dict[int, str] = {}
            for ev in self._llm_events():
                model = self._model_for(ev)
                if not model:
                    per_event[ev.event_id].errors.append("no model name")
                    continue
                try:
                    out = self.invoker(model, self._prompt_for(ev))
                except Exception as exc:
                    per_event[ev.event_id].errors.append(str(exc))
                    continue
                new_outputs[ev.event_id] = out
                per_event[ev.event_id].new_outputs.append(out)
            synthetic = self._synthetic_trace_with_outputs(new_outputs)
            run_patterns = {
                p.pattern_type.value for p in PatternDetector(synthetic).detect_all()
            }
            recurring = original_patterns & run_patterns
            if recurring:
                result.reproduced_count += 1
            per_run_persistent.append(recurring)

        result.per_event = list(per_event.values())
        if per_run_persistent:
            # Persistent = present in every run. Cleared = in original but in no run.
            result.persistent_patterns = set.intersection(*per_run_persistent) if per_run_persistent else set()
            seen_anywhere = set().union(*per_run_persistent)
            result.cleared_patterns = original_patterns - seen_anywhere
        return result
