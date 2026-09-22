"""
Shared analysis types, report quality checks, and deterministic (no-LLM) analysis.

LangGraph / LangChain code lives in :mod:`src.analysis.llm_agent` and loads only when
LLM analysis runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.ingestion import TraceNormalizer
from src.output.deterministic_report import render_deterministic_markdown
from src.preanalysis import RootCauseBuilder
from src.schema import Trace


@dataclass
class AnalysisResult:
    """Result of the analysis."""

    report: str
    trace_summary: dict
    preanalysis: dict
    success: bool
    error: str | None = None


class ReportQualityValidator:
    """Deterministic validator/scorer for generated analysis reports."""

    _SECTION_PATTERNS = {
        "summary": r"(?im)^##?\s*summary\b",
        "timeline": r"(?im)^##?\s*(timeline|what happened)\b",
        "root_cause": r"(?im)^##?\s*root cause",
        "fixes": r"(?im)^##?\s*fix recommendations?",
        "confidence": r"(?im)^##?\s*confidence\b",
    }
    _EVENT_CITATION_RE = re.compile(r"\bEvent(?:s)?\s+\d+(?:\s*-\s*\d+)?\b", re.IGNORECASE)
    _ACTION_RE = re.compile(
        r"\b(add|implement|set|limit|validate|guard|retry|escalate|cache|monitor|truncate|summarize)\b",
        re.IGNORECASE,
    )

    @classmethod
    def validate(cls, report: str) -> dict[str, Any]:
        """Score report quality on completeness, specificity, and actionability."""
        text = report or ""
        section_hits = {
            section: bool(re.search(pattern, text))
            for section, pattern in cls._SECTION_PATTERNS.items()
        }
        completeness = sum(1 for present in section_hits.values() if present) / len(section_hits)

        citations = cls._EVENT_CITATION_RE.findall(text)
        has_event_citations = len(citations) > 0
        if len(citations) >= 4:
            specificity = 1.0
        elif len(citations) >= 2:
            specificity = 0.75
        elif len(citations) == 1:
            specificity = 0.5
        else:
            specificity = 0.1

        has_root_cause = bool(re.search(r"\broot cause\b", text, re.IGNORECASE))
        has_fix_recommendations = bool(re.search(r"\bfix recommendations?\b", text, re.IGNORECASE))
        action_verbs = cls._ACTION_RE.findall(text)
        if len(action_verbs) >= 6:
            actionability = 1.0
        elif len(action_verbs) >= 3:
            actionability = 0.75
        elif len(action_verbs) >= 1:
            actionability = 0.5
        else:
            actionability = 0.2

        overall = (completeness * 0.4) + (specificity * 0.3) + (actionability * 0.3)
        missing_sections = [name for name, present in section_hits.items() if not present]

        return {
            "overall_score": round(overall, 3),
            "completeness": round(completeness, 3),
            "specificity": round(specificity, 3),
            "actionability": round(actionability, 3),
            "has_event_citations": has_event_citations,
            "has_root_cause": has_root_cause,
            "has_fix_recommendations": has_fix_recommendations,
            "missing_sections": missing_sections,
            "citation_count": len(citations),
        }

    @classmethod
    def build_feedback(cls, quality: dict[str, Any]) -> str:
        """Build concise feedback for revision pass."""
        feedback: list[str] = []
        if quality.get("missing_sections"):
            feedback.append(
                "Add missing sections: " + ", ".join(quality["missing_sections"]) + "."
            )
        if not quality.get("has_event_citations"):
            feedback.append("Cite concrete evidence using explicit event IDs for every major claim.")
        if not quality.get("has_root_cause"):
            feedback.append("Include a clear root cause chain, not just symptoms.")
        if not quality.get("has_fix_recommendations"):
            feedback.append("Include concrete fix recommendations grouped by category.")
        if quality.get("actionability", 0) < 0.65:
            feedback.append("Make recommendations implementation-ready with explicit actions.")
        if not feedback:
            feedback.append("Improve precision and specificity while preserving structure.")
        return " ".join(feedback)


def run_analysis_without_llm(trace: Trace, *, show_cost: bool = True) -> AnalysisResult:
    """Deterministic analysis: pattern detection + structured markdown report (no LLM)."""
    trace_summary = TraceNormalizer.get_summary(trace)
    preanalysis = RootCauseBuilder(trace).build()
    report = render_deterministic_markdown(trace, preanalysis, show_cost=show_cost)
    return AnalysisResult(
        report=report,
        trace_summary=trace_summary,
        preanalysis=preanalysis.to_dict(),
        success=True,
    )
