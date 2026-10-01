"""
LLM-powered analysis (LangGraph). Imported only when LLM analysis runs.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.base import BaseMessageChunk
from langchain_core.messages.utils import message_chunk_to_message
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent_autopsy.analysis.citation_validate import validate_report_event_citations
from agent_autopsy.ingestion import TraceNormalizer
from agent_autopsy.preanalysis import RootCauseBuilder
from agent_autopsy.schema import Trace
from agent_autopsy.tracing import TraceSaver, end_trace, get_trace_config, start_trace
from agent_autopsy.utils.config import get_config

from .agent import AnalysisResult, ReportQualityValidator
from .prompts import SYSTEM_PROMPT, get_analysis_prompt, get_final_report_prompt
from .tools import TOOL_DEFINITIONS, AnalysisToolkit

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """State for the analysis agent."""

    messages: Annotated[list, add_messages]
    trace_summary: dict
    preanalysis: dict
    investigation_iterations: int
    report_revisions: int
    token_usage_estimate: int
    token_budget_warning: bool
    report_quality: dict[str, Any]
    analysis_complete: bool
    final_report: str


class AnalysisAgent:
    """
    LLM-powered agent for trace analysis.

    Uses ReAct pattern with guarded tool calling.
    """

    def __init__(
        self,
        trace: Trace,
        model: str | None = None,
        verbose: bool = False,
    ):
        self.trace = trace
        self.config = get_config()
        self.model_name = model or self.config.default_model
        self.verbose = verbose

        self.toolkit = AnalysisToolkit(trace)
        self.llm = self._create_llm()
        self.graph = self._build_graph()

    def _create_llm(self) -> Any:
        """Create the LLM client (OpenRouter-compatible OpenAI API or other providers)."""
        cfg = self.config
        provider = (cfg.llm_provider or "openrouter").lower().strip()
        temperature = 0.1
        max_tokens = cfg.max_tokens
        timeout = cfg.timeout_seconds

        try:
            from langchain.chat_models import init_chat_model
        except ImportError:
            init_chat_model = None

        if init_chat_model is not None:
            if provider == "openrouter":
                return init_chat_model(
                    self.model_name,
                    model_provider="openai",
                    api_key=cfg.openrouter_api_key or None,
                    base_url=cfg.openrouter_base_url,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            if provider == "openai":
                key = cfg.openai_api_key or os.getenv("OPENAI_API_KEY", "")
                base = cfg.openai_api_base or os.getenv("OPENAI_API_BASE") or None
                return init_chat_model(
                    self.model_name,
                    model_provider="openai",
                    api_key=key or None,
                    base_url=base,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            if provider == "anthropic":
                key = os.getenv("ANTHROPIC_API_KEY", "")
                return init_chat_model(
                    f"anthropic:{self.model_name}",
                    api_key=key or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            if provider == "ollama":
                return init_chat_model(
                    f"ollama:{self.model_name}",
                    base_url=cfg.ollama_base_url,
                    temperature=temperature,
                )

        if provider not in ("openrouter", "openai"):
            logger.warning(
                "langchain package not installed or provider %r unsupported without it; "
                "using OpenRouter-compatible OpenAI client",
                provider,
            )
        return ChatOpenAI(
            model=self.model_name,
            openai_api_key=cfg.openrouter_api_key,
            openai_api_base=cfg.openrouter_base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state machine."""
        graph = StateGraph(AgentState)

        graph.add_node("analyze", self._analyze_node)
        graph.add_node("execute_tools", self._execute_tools_node)
        graph.add_node("generate_report", self._generate_report_node)

        graph.set_entry_point("analyze")

        graph.add_conditional_edges(
            "analyze",
            self._should_continue,
            {
                "tools": "execute_tools",
                "report": "generate_report",
                "end": END,
            },
        )
        graph.add_edge("execute_tools", "analyze")
        graph.add_edge("generate_report", END)

        return graph.compile()

    def _finalize_report_markdown(self, text: str) -> str:
        """Append validated structured JSON appendix when the model included a fenced block."""
        from agent_autopsy.analysis.structured_report import (
            extract_structured_json,
            structured_to_markdown_append,
            validate_structured_against_trace,
        )

        structured = extract_structured_json(text)
        if structured is None:
            return text
        errs = validate_structured_against_trace(structured, self.trace)
        return text + structured_to_markdown_append(structured, errs)

    def _analyze_node(self, state: AgentState) -> AgentState:
        """Main analysis node - calls LLM to reason about trace."""
        messages = state["messages"]
        investigation_iterations = state.get("investigation_iterations", 0)
        token_usage_estimate = state.get("token_usage_estimate", 0)
        budget = max(1, self.config.analysis_token_budget)

        if token_usage_estimate >= budget:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Token budget reached for investigation pass. "
                            "Proceeding to synthesis using collected evidence."
                        )
                    )
                ],
                "token_budget_warning": True,
            }

        llm_with_tools = self.llm.bind_tools(
            TOOL_DEFINITIONS,
            tool_choice="auto",
        )

        try:
            if self.config.analysis_use_llm_stream:
                try:
                    response = self._stream_chat_to_message(
                        llm_with_tools, messages, node_name="analyze"
                    )
                except Exception as stream_err:
                    logger.warning(
                        "LLM stream failed in analyze node, falling back to invoke: %s",
                        stream_err,
                    )
                    response = llm_with_tools.invoke(messages)
            else:
                response = llm_with_tools.invoke(messages)
            token_usage_estimate += self._estimate_message_tokens(response)
            return {
                "messages": [response],
                "investigation_iterations": investigation_iterations + 1,
                "token_usage_estimate": token_usage_estimate,
                "token_budget_warning": token_usage_estimate >= int(budget * 0.85),
            }
        except Exception as e:
            logger.exception("LLM invoke failed in analysis node (investigation pass)")
            error_msg = f"LLM error: {e!s}"
            if self.verbose:
                print(f"Warning: {error_msg}")
            return {
                "messages": [AIMessage(content=f"Analysis error: {error_msg}")],
                "analysis_complete": True,
            }

    def _execute_tools_node(self, state: AgentState) -> AgentState:
        """Execute tools requested by the LLM."""
        last_message = state["messages"][-1]
        token_usage_estimate = state.get("token_usage_estimate", 0)

        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return state

        tool_messages = []

        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            if self.verbose:
                print(f"Executing tool: {tool_name}({tool_args})")

            result = self._execute_tool(tool_name, tool_args)
            rendered = json.dumps(result, default=str)
            token_usage_estimate += self._estimate_text_tokens(rendered)

            tool_messages.append(
                ToolMessage(
                    content=rendered,
                    tool_call_id=tool_call.get("id", ""),
                )
            )

        return {
            "messages": tool_messages,
            "token_usage_estimate": token_usage_estimate,
        }

    def _execute_tool(self, tool_name: str, args: dict) -> Any:
        """Execute a single tool and return the result."""
        tool_map = {
            "get_trace_summary": lambda: self.toolkit.get_trace_summary(),
            "get_event": lambda: self.toolkit.get_event(args.get("event_id", 0)),
            "get_events_range": lambda: self.toolkit.get_events_range(
                args.get("start_id", 0), args.get("end_id", 0)
            ),
            "find_errors": lambda: self.toolkit.find_errors(),
            "find_loops": lambda: self.toolkit.find_loops(),
            "find_tool_calls": lambda: self.toolkit.find_tool_calls(args.get("tool_name")),
            "compare_events": lambda: self.toolkit.compare_events(
                args.get("event_id_1", 0), args.get("event_id_2", 0)
            ),
            "get_context_at_event": lambda: self.toolkit.get_context_at_event(
                args.get("event_id", 0), args.get("window", 3)
            ),
            "get_contract_violations": lambda: self.toolkit.get_contract_violations(),
            "get_preanalysis_bundle": lambda: self.toolkit.get_preanalysis_bundle(),
            "get_all_patterns": lambda: self.toolkit.get_all_patterns(),
        }

        if tool_name not in tool_map:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            return tool_map[tool_name]()
        except Exception as e:
            logger.exception("Analysis toolkit tool %r failed", tool_name)
            return {"error": f"Tool execution failed: {e!s}"}

    def _generate_report_node(self, state: AgentState) -> AgentState:
        """Generate the final structured report."""
        base_messages = list(state["messages"])
        synthesis_prompt = (
            "You are now in synthesis pass. First pass investigation is complete.\n"
            "Use only evidence already gathered from tool calls and pre-analysis.\n\n"
            + get_final_report_prompt()
        )
        base_messages.append(HumanMessage(content=synthesis_prompt))
        max_revisions = max(0, self.config.analysis_max_report_revisions)
        target_quality = self.config.analysis_report_quality_threshold

        best_report = ""
        best_quality: dict[str, Any] = {"overall_score": -1.0}
        report_revisions = state.get("report_revisions", 0)
        revision_messages = base_messages

        try:
            for revision in range(max_revisions + 1):
                if self.config.analysis_use_llm_stream:
                    try:
                        response = self._stream_chat_to_message(
                            self.llm, revision_messages, node_name="generate_report"
                        )
                    except Exception as stream_err:
                        logger.warning(
                            "LLM stream failed in report node, falling back to invoke: %s",
                            stream_err,
                        )
                        response = self.llm.invoke(revision_messages)
                else:
                    response = self.llm.invoke(revision_messages)
                candidate = response.content if isinstance(response.content, str) else str(response.content)
                quality = ReportQualityValidator.validate(candidate)

                if quality.get("overall_score", 0.0) > best_quality.get("overall_score", -1.0):
                    best_report = candidate
                    best_quality = quality

                report_revisions += 1
                if (
                    quality.get("overall_score", 0.0) >= target_quality
                    and quality.get("has_event_citations")
                    and quality.get("has_root_cause")
                    and quality.get("has_fix_recommendations")
                ):
                    final = self._finalize_report_markdown(candidate)
                    return {
                        "messages": [AIMessage(content=final)],
                        "final_report": final,
                        "report_revisions": report_revisions,
                        "report_quality": quality,
                        "analysis_complete": True,
                    }

                if revision < max_revisions:
                    feedback = ReportQualityValidator.build_feedback(quality)
                    revision_messages = revision_messages + [
                        AIMessage(content=candidate),
                        HumanMessage(
                            content=(
                                "Revise the report to pass quality gate.\n"
                                f"Feedback: {feedback}\n"
                                "Keep all sections and improve specificity/actionability."
                            )
                        ),
                    ]

            quality_note = (
                "\n\n---\n"
                f"Quality gate warning: best score {best_quality.get('overall_score', 0):.2f} "
                f"(target {target_quality:.2f})."
            )
            combined = best_report + quality_note
            final = self._finalize_report_markdown(combined)
            return {
                "messages": [AIMessage(content=final)],
                "final_report": final,
                "report_revisions": report_revisions,
                "report_quality": best_quality,
                "analysis_complete": True,
            }
        except Exception as e:
            logger.exception("LLM report generation node failed")
            return {
                "final_report": f"Report generation failed: {e!s}",
                "analysis_complete": True,
            }

    def _should_continue(self, state: AgentState) -> str:
        """Determine next step based on state."""
        if state.get("analysis_complete"):
            return "end"

        last_message = state["messages"][-1]

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            if state.get("investigation_iterations", 0) >= self.config.analysis_max_iterations:
                return "report"
            if state.get("token_usage_estimate", 0) >= self.config.analysis_token_budget:
                return "report"
            return "tools"

        if state.get("investigation_iterations", 0) >= self.config.analysis_max_iterations:
            return "report"

        if state.get("token_usage_estimate", 0) >= self.config.analysis_token_budget:
            return "report"

        content = last_message.content.lower() if hasattr(last_message, "content") else ""
        if any(
            phrase in content
            for phrase in [
                "root cause",
                "recommendation",
                "fix",
                "in conclusion",
                "summary",
            ]
        ):
            return "report"

        return "report"

    def _estimate_message_tokens(self, message: Any) -> int:
        """Estimate token cost from a message object."""
        content = ""
        if hasattr(message, "content"):
            content = message.content if isinstance(message.content, str) else str(message.content)
        return self._estimate_text_tokens(content)

    def _estimate_text_tokens(self, text: str) -> int:
        """Approximate token usage using a character heuristic."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    @staticmethod
    def _stringify_stream_content(content: Any) -> str:
        """Normalize streamed message content blocks to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
            return "".join(parts)
        return ""

    def _optional_stream_writer(self) -> Any:
        """LangGraph stream writer when running inside ``graph.stream`` / ``invoke``; else None."""
        try:
            from langgraph.config import get_stream_writer

            return get_stream_writer()
        except RuntimeError:
            return None

    def _stream_chat_to_message(self, runnable: Any, messages: list[Any], *, node_name: str) -> Any:
        """
        Consume ``runnable.stream(messages)``, emit token deltas via LangGraph custom stream,
        and return a finalized :class:`~langchain_core.messages.BaseMessage`.
        """
        writer = self._optional_stream_writer()
        if writer:
            try:
                writer({"kind": "node_start", "node": node_name})
            except Exception:
                logger.debug("StreamWriter node_start emit failed", exc_info=True)

        accumulated: BaseMessageChunk | None = None
        chunk_count = 0
        for chunk in runnable.stream(messages):
            chunk_count += 1
            if writer and isinstance(chunk, BaseMessageChunk):
                delta = self._stringify_stream_content(chunk.content)
                if delta:
                    try:
                        writer({"kind": "llm_token", "node": node_name, "text": delta})
                    except Exception:
                        logger.debug("StreamWriter llm_token emit failed", exc_info=True)
            if accumulated is None:
                accumulated = chunk
            else:
                accumulated = accumulated + chunk

        if accumulated is None or chunk_count == 0:
            raise ValueError("LLM stream produced no chunks")

        return message_chunk_to_message(accumulated)

    def _prepare_graph_run(
        self,
    ) -> tuple[AgentState, dict[str, Any], Any]:
        """Build initial LangGraph state, trace summary, and preanalysis bundle."""
        trace_summary = TraceNormalizer.get_summary(self.trace)
        preanalysis = RootCauseBuilder(self.trace).build()
        initial_prompt = get_analysis_prompt(trace_summary, preanalysis.summary)

        initial_state: AgentState = {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=initial_prompt),
            ],
            "trace_summary": trace_summary,
            "preanalysis": preanalysis.to_dict(),
            "investigation_iterations": 0,
            "report_revisions": 0,
            "token_usage_estimate": self._estimate_text_tokens(initial_prompt),
            "token_budget_warning": False,
            "report_quality": {},
            "analysis_complete": False,
            "final_report": "",
        }
        return initial_state, trace_summary, preanalysis

    def _graph_run_config(self, trace_handler: TraceSaver | None) -> dict[str, Any]:
        cfg: dict[str, Any] = {}
        if trace_handler:
            cfg["callbacks"] = [trace_handler]
            cfg["run_name"] = "agent_autopsy_analysis"
        return cfg

    def iter_stream_report_text(
        self,
        trace_handler: TraceSaver | None,
        result_holder: dict[str, Any],
    ) -> Iterator[str]:
        """
        Stream human-readable LLM output while the analysis graph runs.

        Uses LangGraph ``stream_mode=['values','custom']``. LLM nodes emit token deltas
        via :func:`langgraph.config.get_stream_writer` (``kind: llm_token``); ``values``
        carries budget warnings and final state. On completion, sets
        ``result_holder['result']`` to an :class:`AnalysisResult`.
        """
        initial_state, trace_summary, preanalysis = self._prepare_graph_run()
        run_cfg = self._graph_run_config(trace_handler)
        config = run_cfg if run_cfg else None

        last_values: dict[str, Any] | None = None
        budget_notice = False

        def finish_from_state(final_state: dict[str, Any]) -> AnalysisResult:
            report = final_state.get("final_report") or ""
            return AnalysisResult(
                report=report,
                trace_summary=trace_summary,
                preanalysis=preanalysis.to_dict(),
                success=True,
            )

        try:
            for mode, chunk in self.graph.stream(
                initial_state,
                config=config,
                stream_mode=["values", "custom"],
            ):
                if mode == "custom" and isinstance(chunk, dict):
                    kind = chunk.get("kind")
                    if kind == "node_start":
                        node = chunk.get("node") or ""
                        if isinstance(node, str) and node:
                            yield f"\n\n**[{node}]**\n\n"
                    elif kind == "llm_token":
                        text = chunk.get("text", "")
                        if isinstance(text, str) and text:
                            yield text
                elif mode == "values" and isinstance(chunk, dict):
                    last_values = chunk
                    if chunk.get("token_budget_warning") and not budget_notice:
                        budget_notice = True
                        yield "\n\n_⚠ Token budget warning — investigation may wrap up soon._\n\n"

            final_state = last_values
            if final_state is None:
                final_state = self.graph.invoke(initial_state, config=config)

            res = finish_from_state(final_state)

            cite_errs = validate_report_event_citations(res.report, self.trace)
            if cite_errs:
                note = "\n\n---\n## Citation validation\n\n" + "\n".join(f"- {e}" for e in cite_errs)
                res = AnalysisResult(
                    report=res.report + note,
                    trace_summary=res.trace_summary,
                    preanalysis=res.preanalysis,
                    success=res.success,
                    error=res.error,
                )
            result_holder["result"] = res
        except Exception as e:
            logger.exception("Streaming analysis graph failed")
            if trace_handler:
                trace_handler.add_error_event(e, context="agent_stream")
            result_holder["result"] = AnalysisResult(
                report="",
                trace_summary=trace_summary,
                preanalysis=preanalysis.to_dict(),
                success=False,
                error=str(e),
            )
            yield f"\n\n**Analysis failed:** {e}\n"

    def run(self, trace_handler: TraceSaver | None = None) -> AnalysisResult:
        """Run the analysis and return results."""
        initial_state, trace_summary, preanalysis = self._prepare_graph_run()
        config = self._graph_run_config(trace_handler)

        try:
            final_state = self.graph.invoke(initial_state, config=config if config else None)

            return AnalysisResult(
                report=final_state.get("final_report", ""),
                trace_summary=trace_summary,
                preanalysis=preanalysis.to_dict(),
                success=True,
            )
        except Exception as e:
            logger.exception("Analysis graph invocation failed")
            if trace_handler:
                trace_handler.add_error_event(e, context="agent_run")
            return AnalysisResult(
                report="",
                trace_summary=trace_summary,
                preanalysis=preanalysis.to_dict(),
                success=False,
                error=str(e),
            )


def run_analysis(
    trace: Trace,
    model: str | None = None,
    verbose: bool = False,
    enable_tracing: bool | None = None,
) -> AnalysisResult:
    """Convenience function to run LLM analysis on a trace."""
    agent = AnalysisAgent(trace, model=model, verbose=verbose)

    trace_config = get_trace_config()
    should_trace = enable_tracing if enable_tracing is not None else trace_config.enabled

    trace_handler = None
    if should_trace:
        trace_handler, _run_id = start_trace()

    try:
        result = agent.run(trace_handler=trace_handler)

        cite_errs = validate_report_event_citations(result.report, trace)
        if cite_errs:
            note = "\n\n---\n## Citation validation\n\n" + "\n".join(f"- {e}" for e in cite_errs)
            result = AnalysisResult(
                report=result.report + note,
                trace_summary=result.trace_summary,
                preanalysis=result.preanalysis,
                success=result.success,
                error=result.error,
            )
        return result
    except Exception as e:
        logger.exception("run_analysis failed before trace teardown")
        if trace_handler:
            trace_handler.add_error_event(e, context="run_analysis")
        raise
    finally:
        if trace_handler:
            end_trace(trace_handler)


def run_analysis_stream(
    trace: Trace,
    result_holder: dict[str, Any],
    model: str | None = None,
    verbose: bool = False,
    enable_tracing: bool | None = None,
) -> Iterator[str]:
    """Run LLM analysis while streaming text fragments (for UIs)."""
    agent = AnalysisAgent(trace, model=model, verbose=verbose)

    trace_config = get_trace_config()
    should_trace = enable_tracing if enable_tracing is not None else trace_config.enabled

    trace_handler = None
    if should_trace:
        trace_handler, _run_id = start_trace()

    try:
        yield from agent.iter_stream_report_text(trace_handler, result_holder)
    except Exception as e:
        logger.exception("run_analysis_stream failed before trace teardown")
        if trace_handler:
            trace_handler.add_error_event(e, context="run_analysis_stream")
        if "result" not in result_holder:
            ts = TraceNormalizer.get_summary(trace)
            pa = RootCauseBuilder(trace).build()
            result_holder["result"] = AnalysisResult(
                report="",
                trace_summary=ts,
                preanalysis=pa.to_dict(),
                success=False,
                error=str(e),
            )
        raise
    finally:
        if trace_handler:
            end_trace(trace_handler)
