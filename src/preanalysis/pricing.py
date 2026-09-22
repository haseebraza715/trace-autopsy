"""
Model pricing and per-trace cost computation.

Prices are USD per 1 million tokens (input, output), captured around early 2026.
They are an approximate, point-in-time snapshot — update from provider pricing
pages when accuracy matters. Unknown models return None rather than guess.

Sources to refresh from:
- OpenAI:    https://openai.com/api/pricing
- Anthropic: https://www.anthropic.com/pricing
- Google:    https://ai.google.dev/pricing
- OpenRouter: https://openrouter.ai/models
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from src.schema import Trace, TraceEvent, EventType


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for input and output."""

    input_per_mtok: float
    output_per_mtok: float


# Canonical key → ModelPrice. Keys are normalized lowercase substrings.
MODEL_PRICING: dict[str, ModelPrice] = {
    # OpenAI
    "gpt-5":            ModelPrice(1.25, 10.00),
    "gpt-4.1-mini":     ModelPrice(0.40,  1.60),
    "gpt-4.1":          ModelPrice(2.00,  8.00),
    "gpt-4o-mini":      ModelPrice(0.15,  0.60),
    "gpt-4o":           ModelPrice(2.50, 10.00),
    "gpt-4-turbo":      ModelPrice(10.00, 30.00),
    "gpt-4":            ModelPrice(30.00, 60.00),  # legacy 8k/32k
    "gpt-3.5-turbo":    ModelPrice(0.50,  1.50),
    "o1-mini":          ModelPrice(3.00, 12.00),
    "o1":               ModelPrice(15.00, 60.00),
    # Anthropic Claude 4.x
    "claude-opus-4":    ModelPrice(15.00, 75.00),
    "claude-sonnet-4":  ModelPrice(3.00, 15.00),
    "claude-haiku-4":   ModelPrice(0.80,  4.00),
    # Anthropic Claude 3.x (still common in old traces)
    "claude-3-5-sonnet": ModelPrice(3.00, 15.00),
    "claude-3-5-haiku":  ModelPrice(0.80,  4.00),
    "claude-3-opus":     ModelPrice(15.00, 75.00),
    # Google Gemini
    "gemini-2.5-pro":   ModelPrice(1.25,  5.00),
    "gemini-2.5-flash": ModelPrice(0.30,  2.50),
    "gemini-2.0-flash": ModelPrice(0.10,  0.40),
    "gemini-1.5-pro":   ModelPrice(1.25,  5.00),
    # Free / self-hosted
    "gemma":            ModelPrice(0.0, 0.0),
    "llama":            ModelPrice(0.0, 0.0),
    "mistral":          ModelPrice(0.0, 0.0),
    "qwen":             ModelPrice(0.0, 0.0),
}

# Default split of token_count when input/output aren't separately reported.
# Most agent runs are prompt-heavy (long context, short generations).
_DEFAULT_INPUT_RATIO = 0.7


@dataclass
class CostBreakdown:
    """Cost summary for a trace."""

    total_usd: float = 0.0
    waste_usd: float = 0.0
    priced_events: int = 0
    unpriced_events: int = 0  # LLM events whose model couldn't be priced
    by_model: dict[str, float] = field(default_factory=dict)
    waste_ratio: float = 0.0  # waste_usd / total_usd, 0..1

    @property
    def is_priced(self) -> bool:
        return self.priced_events > 0

    def extrapolate_per_day(self, runs_per_day: int) -> float:
        """Extrapolate wasted spend across a daily volume."""
        return self.waste_usd * runs_per_day


def _normalize(name: str) -> str:
    """Lowercase, strip provider prefix and OpenRouter suffix tags."""
    n = name.lower().strip()
    # Strip OpenRouter / provider prefix, e.g. "openai/gpt-4o" or "google/gemma-7b-it:free"
    if "/" in n:
        n = n.split("/", 1)[1]
    # Strip OpenRouter suffix tags like ":free", ":nitro", ":beta"
    if ":" in n:
        n = n.split(":", 1)[0]
    return n


def match_model(name: str | None) -> tuple[str, ModelPrice] | None:
    """
    Fuzzy-match a model name to a pricing entry.

    Returns (canonical_key, price) or None when no match is confident.
    """
    if not name:
        return None
    norm = _normalize(name)
    # Prefer the longest matching key — avoids "gpt-4" matching "gpt-4o-mini"
    matches = [k for k in MODEL_PRICING if k in norm]
    if not matches:
        return None
    best = max(matches, key=len)
    return best, MODEL_PRICING[best]


def event_token_split(event: TraceEvent) -> tuple[int, int] | None:
    """
    Extract (input_tokens, output_tokens) from an event.

    Prefers explicit metadata keys (from OTel GenAI / OpenInference parsers),
    falls back to splitting `token_count` by a default 70/30 input/output ratio.
    Returns None when no token information is available at all.
    """
    md = event.metadata or {}
    # Common keys across OTel GenAI, OpenInference, traceloop, raw OpenAI/Anthropic
    in_keys  = ("input_tokens", "prompt_tokens", "gen_ai.usage.input_tokens", "llm.token_count.prompt")
    out_keys = ("output_tokens", "completion_tokens", "gen_ai.usage.output_tokens", "llm.token_count.completion")
    in_tok  = next((int(md[k]) for k in in_keys  if k in md and md[k] is not None), None)
    out_tok = next((int(md[k]) for k in out_keys if k in md and md[k] is not None), None)
    if in_tok is not None and out_tok is not None:
        return in_tok, out_tok
    if event.token_count:
        in_split  = int(event.token_count * _DEFAULT_INPUT_RATIO)
        out_split = event.token_count - in_split
        return in_split, out_split
    return None


def event_cost(event: TraceEvent, fallback_model: str | None = None) -> float | None:
    """USD cost for a single LLM event, or None if not priceable."""
    if event.type != EventType.LLM_CALL:
        return None
    model_name = event.name or fallback_model
    matched = match_model(model_name)
    if matched is None:
        return None
    _, price = matched
    split = event_token_split(event)
    if split is None:
        return None
    in_tok, out_tok = split
    return (in_tok * price.input_per_mtok + out_tok * price.output_per_mtok) / 1_000_000.0


def compute_trace_cost(
    trace: Trace,
    waste_event_ids: Iterable[int] | None = None,
) -> CostBreakdown:
    """
    Sum cost across all priceable LLM events, with a separate 'waste' total
    derived from event IDs flagged by detectors (e.g. token_waste, retry_storm,
    infinite_loop).
    """
    waste_set = set(waste_event_ids or [])
    breakdown = CostBreakdown()
    fallback_model = trace.env.model if trace.env else None

    for ev in trace.events:
        if ev.type != EventType.LLM_CALL:
            continue
        cost = event_cost(ev, fallback_model=fallback_model)
        if cost is None:
            breakdown.unpriced_events += 1
            continue
        breakdown.priced_events += 1
        breakdown.total_usd += cost
        if ev.event_id in waste_set:
            breakdown.waste_usd += cost
        matched = match_model(ev.name or fallback_model)
        if matched:
            key, _ = matched
            breakdown.by_model[key] = breakdown.by_model.get(key, 0.0) + cost

    if breakdown.total_usd > 0:
        breakdown.waste_ratio = breakdown.waste_usd / breakdown.total_usd
    return breakdown


def waste_event_ids_from_signals(signals: list) -> set[int]:
    """
    Collect event IDs implicated by waste-related signals across detector outputs.

    Accepts either PatternResult-shaped objects (with .pattern_type and .event_ids)
    or dict signals (with 'type' and 'events'/'event_ids') so it works against
    both the live PatternDetector output and serialized preanalysis bundles.
    """
    waste_types = {"token_waste", "retry_storm", "infinite_loop", "redundant_tool_call"}
    ids: set[int] = set()
    for sig in signals:
        sig_type = None
        sig_events: list[int] = []
        if hasattr(sig, "pattern_type"):
            sig_type = getattr(sig.pattern_type, "value", str(sig.pattern_type))
            sig_events = list(getattr(sig, "event_ids", []) or [])
        elif hasattr(sig, "type"):
            sig_type_attr = getattr(sig, "type")
            sig_type = getattr(sig_type_attr, "value", str(sig_type_attr))
            sig_events = list(getattr(sig, "event_ids", []) or [])
        elif isinstance(sig, dict):
            sig_type = sig.get("type")
            sig_events = list(sig.get("event_ids") or sig.get("events") or [])
        if sig_type in waste_types:
            ids.update(int(e) for e in sig_events)
    return ids


def format_cost_section(
    breakdown: CostBreakdown,
    runs_per_day: int = 1000,
) -> list[str]:
    """Render a markdown 'Cost' section as list of lines."""
    if not breakdown.is_priced:
        return [
            "## Cost",
            "",
            "_Not computed: no priceable LLM events found "
            "(unknown model or missing token counts)._",
            "",
        ]

    lines = [
        "## Cost",
        "",
        f"- **Run cost:** ${breakdown.total_usd:.4f}",
    ]
    if breakdown.waste_usd > 0:
        lines.append(
            f"- **Wasted:** ${breakdown.waste_usd:.4f} "
            f"({breakdown.waste_ratio:.0%} of run)"
        )
        extrap = breakdown.extrapolate_per_day(runs_per_day)
        lines.append(
            f"- **Extrapolated at {runs_per_day:,} runs/day:** "
            f"${extrap:.2f}/day in waste"
        )
    if breakdown.unpriced_events:
        lines.append(
            f"- **Unpriced LLM events:** {breakdown.unpriced_events} "
            "(model not in pricing table)"
        )
    if breakdown.by_model:
        parts = [f"{k}: ${v:.4f}" for k, v in sorted(breakdown.by_model.items())]
        lines.append(f"- **By model:** {', '.join(parts)}")
    lines.append("")
    return lines
