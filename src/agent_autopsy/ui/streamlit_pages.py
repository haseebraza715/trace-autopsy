"""
Agent Autopsy GUI - Streamlit Application

A user-friendly interface for analyzing agent execution traces.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent_autopsy import api
from agent_autopsy.errors import ParseError, PluginError, SchemaValidationError
from agent_autopsy.ui import theme
from agent_autopsy.utils.config import get_config

# Logging setup
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def configure_page(*, page_title: str = "Agent Autopsy") -> None:
    """Call once at the top of each Streamlit entry script (before other ``st`` calls)."""
    st.set_page_config(
        page_title=page_title,
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.inject_global_styles()


def init_session_state():
    """Initialize session state variables."""
    defaults = {
        "trace": None,
        "preanalysis": None,
        "analysis_result": None,
        "report_markdown": None,
        "report_json": None,
        "recent_reports": [],
        "batch_results": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def get_severity_icon(severity: str) -> str:
    """Get icon for severity level."""
    return {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🔵",
        "low": "⚪",
    }.get(severity.lower(), "⚪")


def get_event_display_name(event) -> str:
    """Get a meaningful display name for an event."""
    name = event.name
    if name and name != "unknown":
        return name

    # Try to extract context from metadata
    if event.metadata:
        node = event.metadata.get("langgraph_node", "")
        if node:
            return f"({node})"
        run_id = event.metadata.get("run_id", "")
        if run_id:
            return f"(run:{run_id[:8]})"

    # Try to extract context from output structure
    if event.output and isinstance(event.output, dict):
        if "analysis_complete" in event.output:
            return "(analysis_result)"
        if "messages" in event.output:
            return "(chain_result)"
        if "trace_summary" in event.output:
            return "(trace_result)"

    return "unnamed"


def load_reports_index() -> list[dict]:
    """Load recent reports from index file."""
    index_path = Path("reports/index.json")
    if index_path.exists():
        try:
            with open(index_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to read reports index at %s", index_path, exc_info=True)
    return []


def save_to_reports_index(report_info: dict):
    """Save report info to index."""
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    index_path = reports_dir / "index.json"

    reports = load_reports_index()
    reports.insert(0, report_info)
    reports = reports[:50]  # Keep last 50

    with open(index_path, "w") as f:
        json.dump(reports, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar_brand() -> None:
    st.markdown(
        """
        <div class="sidebar-brand">
          <span class="sidebar-brand-mark">AA</span>
          <div>
            <div class="sidebar-brand-name">Agent Autopsy</div>
            <div class="sidebar-brand-sub">Trace diagnosis</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_llm_status_card() -> None:
    config = get_config()
    if api.llm_credentials_configured(config):
        badge = theme.status_badge("success", "Configured")
        body = f"<b style='color:var(--aa-text)'>LLM</b> provider <code style='color:var(--aa-muted);font-family:var(--aa-mono);font-size:11px'>{config.llm_provider}</code> ready."
    else:
        badge = theme.status_badge("warning", "Not configured")
        body = "<b style='color:var(--aa-text)'>LLM</b> disabled for current provider. Set API keys in <code style='color:var(--aa-muted);font-family:var(--aa-mono);font-size:11px'>.env</code> (see Settings)."
    st.markdown(
        f"""
        <div class="aa-card" style="padding:.85rem .9rem;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem;">
            <span class="aa-sidebar-title">LLM status</span>{badge}
          </div>
          <div style="color:var(--aa-muted);font-size:12px;line-height:1.5;">{body}</div>
        </div>
        <style>
        .aa-sidebar-title {{ color: var(--aa-dim); font: 600 9px var(--aa-mono); letter-spacing: .1em; text-transform: uppercase; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar():
    """Render the sidebar navigation and settings."""
    with st.sidebar:
        _render_sidebar_brand()

        st.markdown('<div class="sidebar-heading">Navigation</div>', unsafe_allow_html=True)
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/demo.py", label="Demo", icon=":material/troubleshoot:")
        st.page_link("pages/02_Analyze_Trace.py", label="Analyze Trace", icon="📊")
        st.page_link("pages/03_Trace_Viewer.py", label="Trace Viewer", icon="👁️")
        st.page_link("pages/04_Batch_Analysis.py", label="Batch Analysis", icon="📁")
        st.page_link("pages/05_Reports.py", label="Reports", icon="📝")
        st.page_link("pages/06_Settings.py", label="Settings", icon="⚙️")

        st.markdown('<div class="sidebar-heading">System</div>', unsafe_allow_html=True)
        _render_llm_status_card()

        # Quick stats if trace is loaded
        if st.session_state.trace:
            trace = st.session_state.trace
            st.markdown('<div class="sidebar-heading">Session</div>', unsafe_allow_html=True)
            theme.render_trace_summary_card(
                run_id=trace.run_id,
                status=trace.status.value,
                events=len(trace.events),
            )
            if st.button("Clear Trace", width='stretch'):
                st.session_state.trace = None
                st.session_state.preanalysis = None
                st.session_state.analysis_result = None
                st.session_state.report_markdown = None
                st.session_state.report_json = None
                st.rerun()


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def _render_home_actions() -> None:
    actions = [
        {
            "icon": "▶",
            "title": "Analyze a trace",
            "body": "Upload a single trace JSON and reconstruct exactly where the run failed.",
            "page": "pages/02_Analyze_Trace.py",
            "key": "home_analyze",
            "tone": "accent",
        },
        {
            "icon": "▥",
            "title": "Batch analysis",
            "body": "Run the deterministic pipeline across every trace in a directory at once.",
            "page": "pages/04_Batch_Analysis.py",
            "key": "home_batch",
        },
        {
            "icon": "▤",
            "title": "Browse reports",
            "body": "Open previously generated Markdown and JSON reports from disk.",
            "page": "pages/05_Reports.py",
            "key": "home_reports",
        },
    ]
    cols = st.columns(3, gap="medium")
    for col, action in zip(cols, actions):
        with col:
            tone = action.get("tone")
            tone_style = (
                "border-color:rgba(230,170,74,.4);"
                if tone == "accent"
                else ""
            )
            st.markdown(
                f"""
                <div class="aa-card aa-action-card" style="{tone_style}">
                  <div class="aa-action-icon">{action['icon']}</div>
                  <div class="aa-action-title">{action['title']}</div>
                  <div class="aa-action-body">{action['body']}</div>
                </div>
                <style>
                .aa-action-card {{ min-height: 168px; display: flex; flex-direction: column; gap: .4rem; }}
                .aa-action-icon {{
                  width: 36px; height: 36px; display: grid; place-items: center;
                  border: 1px solid var(--aa-border); background: var(--aa-surface-2);
                  border-radius: 8px; color: var(--aa-accent); font-size: 15px; margin-bottom: .4rem;
                }}
                .aa-action-title {{ color: var(--aa-text); font-size: 15px; font-weight: 620; letter-spacing: -.01em; }}
                .aa-action-body {{ color: var(--aa-muted); font-size: 12.5px; line-height: 1.55; flex: 1; }}
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.button("Open", key=action["key"], width='stretch')
            if st.session_state.get(action["key"]):
                st.switch_page(action["page"])


def _render_recent_files() -> None:
    theme.render_section_header("History", "Recent traces")
    traces_dir = Path("traces")
    if not traces_dir.exists() or not list(traces_dir.glob("*.json")):
        theme.render_empty_state(
            "file",
            "No traces found",
            "Drop trace JSON files into ./traces/ and they will appear here for one-click analysis.",
        )
        return

    trace_files = sorted(traces_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
    for idx, trace_file in enumerate(trace_files):
        mtime = datetime.fromtimestamp(trace_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        c1, c2, c3 = st.columns([5, 2, 1.2])
        with c1:
            st.markdown(
                f'<div style="color:var(--aa-text);font-size:13px;font-weight:550;font-family:var(--aa-mono);">'
                f"{trace_file.name}</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f'<div style="color:var(--aa-dim);font-size:12px;text-align:right;">{mtime}</div>',
                unsafe_allow_html=True,
            )
        with c3:
            if st.button("Analyze", key=f"quick_{idx}_{trace_file.name}", width='stretch'):
                try:
                    st.session_state.trace = api.load_trace(trace_file)
                    st.switch_page("pages/02_Analyze_Trace.py")
                except (ParseError, SchemaValidationError, PluginError) as e:
                    st.error(f"Error loading trace: {e}")
                except Exception:
                    logger.exception("Failed loading recent trace file: %s", trace_file)
                    st.error("Error loading trace (see logs for details).")
        if idx < len(trace_files) - 1:
            st.markdown(
                '<div style="height:1px;background:var(--aa-border-soft);margin:.55rem 0;"></div>',
                unsafe_allow_html=True,
            )


def _render_recent_reports() -> None:
    theme.render_section_header("History", "Recent reports")
    reports = load_reports_index()[:5]
    if not reports:
        theme.render_empty_state(
            "file",
            "No reports yet",
            "Run an analysis and the generated reports will be indexed here for quick access.",
        )
        return

    for idx, report in enumerate(reports):
        run_id = report.get('run_id', 'Unknown')
        generated_at = report.get('generated_at', 'Unknown')[:10]
        signals = report.get('signals', 0)
        hypotheses = report.get('hypotheses', 0)
        status = str(report.get('status', 'unknown'))
        status_badge = theme.status_badge(
            "success" if status.lower() in {"success", "complete"} else "neutral",
            status,
        )
        c1, c2, c3, c4 = st.columns([4, 2, 1.4, 1])
        with c1:
            st.markdown(
                f'<div style="color:var(--aa-text);font-size:13px;font-weight:550;font-family:var(--aa-mono);">'
                f"{run_id[:42]}</div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f'<div style="color:var(--aa-dim);font-size:12px;">{signals} signals · {hypotheses} hypotheses</div>',
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f'<div style="display:flex;justify-content:flex-end;">{status_badge}</div>',
                unsafe_allow_html=True,
            )
        with c4:
            if st.button("View", key=f"view_report_{idx}_{run_id}", width='stretch'):
                st.switch_page("pages/05_Reports.py")
        st.markdown(
            f'<div style="color:var(--aa-dim);font-size:11px;font-family:var(--aa-mono);">'
            f"generated {generated_at}</div>",
            unsafe_allow_html=True,
        )
        if idx < len(reports) - 1:
            st.markdown(
                '<div style="height:1px;background:var(--aa-border-soft);margin:.55rem 0;"></div>',
                unsafe_allow_html=True,
            )


def render_home_page():
    """Render the home/dashboard page."""
    theme.render_hero(
        "Agent Autopsy",
        "Understand why your agent failed.",
        "Load an execution trace and reconstruct the exact sequence of events — root causes, "
        "failure patterns, and recommended fixes — without reading raw JSON.",
    )

    _render_home_actions()
    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.6rem 0;"></div>',
        unsafe_allow_html=True,
    )
    _render_recent_files()
    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.6rem 0;"></div>',
        unsafe_allow_html=True,
    )
    _render_recent_reports()


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def render_analyze_page():
    """Render the analyze trace page."""
    theme.render_hero(
        "Analyze",
        "Inspect a single run.",
        "Upload a trace JSON file or pick one from the traces directory, then run the "
        "deterministic analysis pipeline to identify what went wrong.",
    )

    # File upload or selection
    upload_tab, select_tab = st.tabs(["Upload File", "Select from Directory"])

    with upload_tab:
        uploaded_file = st.file_uploader("Upload trace JSON file", type=["json"], label_visibility="collapsed")
        if uploaded_file:
            with st.status("Loading trace…", expanded=True) as load_status:
                try:
                    content = json.load(uploaded_file)
                    # Save temporarily
                    temp_path = Path(f"/tmp/autopsy_upload_{uploaded_file.name}")
                    with open(temp_path, "w") as f:
                        json.dump(content, f)
                    st.session_state.trace = api.load_trace(temp_path)
                    load_status.update(label="Trace loaded", state="complete")
                    st.success("Trace loaded successfully!")
                except (ParseError, SchemaValidationError, PluginError) as e:
                    st.error(f"Error parsing trace: {e}")
                except Exception:
                    logger.exception("Failed parsing uploaded trace file: %s", uploaded_file.name)
                    st.error("Error parsing trace (see logs for details).")

    with select_tab:
        traces_dir = Path("traces")
        if traces_dir.exists():
            trace_files = list(traces_dir.glob("*.json"))
            if trace_files:
                selected_file = st.selectbox(
                    "Select trace file",
                    options=trace_files,
                    format_func=lambda x: x.name,
                    label_visibility="collapsed",
                )
                st.markdown(
                    f'<div style="color:var(--aa-dim);font-size:12px;margin:-.55rem 0 .6rem;">'
                    f"{len(trace_files)} trace files available</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Load Trace", type="primary"):
                    try:
                        st.session_state.trace = api.load_trace(selected_file)
                        st.success("Trace loaded successfully!")
                        st.rerun()
                    except (ParseError, SchemaValidationError, PluginError) as e:
                        st.error(f"Error parsing trace: {e}")
                    except Exception:
                        logger.exception("Failed parsing selected trace file: %s", selected_file)
                        st.error("Error parsing trace (see logs for details).")
            else:
                theme.render_empty_state(
                    "file",
                    "No trace files found",
                    "Place JSON trace exports in ./traces/ and they will be selectable here.",
                )
        else:
            theme.render_empty_state(
                "file",
                "Traces directory not found",
                "Create ./traces/ and add JSON exports to use directory selection.",
            )

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
        unsafe_allow_html=True,
    )

    # Analysis options
    if st.session_state.trace:
        trace = st.session_state.trace

        theme.render_section_header(
            "Pipeline",
            "Analysis options",
            right=theme.status_badge("info", "Trace loaded"),
        )

        col1, col2 = st.columns(2)

        with col1:
            no_llm = st.checkbox("Deterministic Only (no LLM)", value=False)

        with col2:
            config = get_config()
            model_override = st.text_input("Model Override", value="", placeholder=config.default_model)

        no_embeddings = st.checkbox(
            "Skip semantic embeddings (no sentence-transformers)",
            value=False,
            help="Saves memory when you do not need embedding-based goal drift signals.",
        )

        stream_llm = st.checkbox(
            "Stream LLM output live",
            value=False,
            disabled=no_llm or not api.llm_credentials_configured(config),
            help="Stream model tokens and graph steps with st.write_stream (best-effort; depends on provider streaming).",
        )

        # Run analysis button
        if st.button("Run Analysis", type="primary", width='stretch'):
            prev_skip = config.skip_embeddings
            if no_embeddings:
                config.skip_embeddings = True
            try:
                api.apply_embedding_defaults_for_trace(trace)
                with st.status("Running pre-analysis...", expanded=True) as status_box:
                    status_box.write("Reconstructing execution order…")
                    preanalysis = api.run_preanalysis(trace)
                st.session_state.preanalysis = preanalysis

                model = model_override if model_override else None

                # Full analysis
                if no_llm or not api.llm_credentials_configured(config):
                    with st.spinner("Running deterministic analysis..."):
                        result = api.run_deterministic_analysis(trace)
                elif stream_llm:
                    holder: dict = {}
                    result = None
                    with st.expander("Live LLM stream", expanded=True):
                        try:
                            st.write_stream(
                                api.stream_llm_analysis_text(
                                    trace,
                                    holder,
                                    model=model,
                                    verbose=False,
                                )
                            )
                            result = holder.get("result")
                        except Exception as e:
                            logger.exception(
                                "Streaming LLM analysis failed for run %s",
                                trace.run_id,
                            )
                            st.warning(f"Streaming failed: {e}. Falling back to buffered LLM call.")
                            try:
                                result = api.run_llm_analysis(trace, model=model)
                            except Exception as e2:
                                logger.exception(
                                    "LLM analysis failed for run %s. Falling back to deterministic mode.",
                                    trace.run_id,
                                )
                                st.warning(f"LLM analysis failed: {e2}. Falling back to deterministic.")
                                result = api.run_deterministic_analysis(trace)
                    if result is None:
                        st.warning("Stream ended without a result; running deterministic analysis.")
                        result = api.run_deterministic_analysis(trace)
                else:
                    with st.spinner("Running LLM analysis..."):
                        try:
                            result = api.run_llm_analysis(trace, model=model)
                        except Exception as e:
                            logger.exception(
                                "LLM analysis failed for run %s. Falling back to deterministic mode.",
                                trace.run_id,
                            )
                            st.warning(f"LLM analysis failed: {e}. Falling back to deterministic.")
                            result = api.run_deterministic_analysis(trace)

                st.session_state.analysis_result = result

                with st.spinner("Building report..."):
                    report_gen = api.generate_report(trace, result)
                    st.session_state.report_markdown = report_gen.to_markdown()
                    st.session_state.report_json = report_gen.to_json()

                # Save to reports index
                report_info = {
                    "run_id": trace.run_id,
                    "status": trace.status.value,
                    "generated_at": datetime.now().isoformat(),
                    "signals": len(preanalysis.signals),
                    "hypotheses": len(preanalysis.hypotheses),
                }
                save_to_reports_index(report_info)

                st.success("Analysis complete!")

            except Exception as e:
                logger.exception("Analysis failed for run %s", trace.run_id)
                st.error(f"Analysis failed: {e}")
            finally:
                config.skip_embeddings = prev_skip

        st.markdown(
            '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
            unsafe_allow_html=True,
        )

        # Display results in tabs
        if st.session_state.trace:
            tabs = st.tabs(["Summary", "Signals", "Hypotheses", "Timeline", "Report"])

            with tabs[0]:
                render_summary_tab(trace)

            with tabs[1]:
                render_signals_tab()

            with tabs[2]:
                render_hypotheses_tab()

            with tabs[3]:
                render_timeline_tab(trace)

            with tabs[4]:
                render_report_tab()
    else:
        theme.render_empty_state(
            "upload",
            "Load a trace to begin",
            "Upload a trace JSON in the Upload File tab, or select one from the traces directory. "
            "Then configure the pipeline and run the analysis.",
        )


def render_summary_tab(trace):
    """Render trace summary tab."""
    summary = api.trace_summary(trace)

    status_raw = summary.get("status", "N/A")
    status_badge = theme.status_badge(
        "success" if str(status_raw).lower() in {"success", "complete"} else "neutral",
        str(status_raw),
    )
    st.markdown(
        f"""
        <div class="aa-card-head" style="margin:.4rem 0 .9rem;">
          <div>
            <div class="aa-section-kicker">Overview</div>
            <div class="aa-section-title">Trace summary</div>
          </div>
          {status_badge}
        </div>
        """,
        unsafe_allow_html=True,
    )

    duration = summary.get("duration_ms")
    tokens = summary.get("total_tokens")
    theme.render_stat_cards(
        [
            {"label": "Total events", "value": summary.get("total_events", 0)},
            {"label": "Errors", "value": summary.get("errors", 0), "tone": "error" if summary.get("errors", 0) else None},
            {"label": "Duration", "value": f"{duration}ms" if duration else "N/A"},
            {"label": "Framework", "value": summary.get("framework", "N/A")},
            {"label": "LLM calls", "value": summary.get("llm_calls", 0)},
            {"label": "Tool calls", "value": summary.get("tool_calls", 0)},
            {"label": "Total tokens", "value": tokens if tokens else "N/A"},
            {"label": "Status", "value": status_raw},
        ],
        columns=4,
    )

    with st.expander("Full details"):
        st.json(summary)


def render_signals_tab():
    """Render signals tab."""
    theme.render_section_header("Findings", "Detected signals")

    if not st.session_state.preanalysis:
        theme.render_empty_state(
            "play",
            "No signals yet",
            "Run the analysis pipeline to detect behavioral signals in this trace.",
        )
        return

    signals = st.session_state.preanalysis.signals

    if not signals:
        st.markdown(
            '<div style="border:1px solid rgba(103,189,145,.3);background:rgba(103,189,145,.07);'
            'border-radius:8px;padding:.9rem 1rem;color:#67bd91;font-size:13px;font-weight:550;">'
            "✓ No significant issues detected.</div>",
            unsafe_allow_html=True,
        )
        return

    for signal in signals:
        severity = str(signal.severity)
        expanded = severity in ["critical", "high"]
        with st.expander(f"{signal.type.replace('_', ' ').title()} · {severity.upper()}", expanded=expanded):
            header_col, badge_col = st.columns([3, 1])
            with badge_col:
                st.markdown(
                    f'<div style="display:flex;justify-content:flex-end;">{theme.severity_badge(severity)}</div>',
                    unsafe_allow_html=True,
                )
            with header_col:
                st.markdown(
                    f"<div style='color:var(--aa-text);font-size:13.5px;font-weight:550;'>"
                    f"{signal.type.replace('_', ' ').title()}</div>",
                    unsafe_allow_html=True,
                )
            st.markdown(f"**Evidence:** {signal.evidence}")
            st.markdown(
                f"<span style='color:var(--aa-dim);font-size:12px;'>Related events: "
                f"<code style='font-family:var(--aa-mono);color:var(--aa-muted);'>{signal.event_ids}</code></span>",
                unsafe_allow_html=True,
            )
            if signal.metadata:
                with st.expander("Signal metadata"):
                    st.json(signal.metadata)


def render_hypotheses_tab():
    """Render hypotheses tab."""
    theme.render_section_header("Findings", "Root cause hypotheses")

    if not st.session_state.preanalysis:
        theme.render_empty_state(
            "play",
            "No hypotheses yet",
            "Run the analysis pipeline to generate root cause hypotheses.",
        )
        return

    hypotheses = st.session_state.preanalysis.hypotheses

    if not hypotheses:
        theme.render_empty_state(
            "check",
            "No hypotheses generated",
            "The deterministic pipeline found no candidate root causes for this trace.",
        )
        return

    for i, hyp in enumerate(hypotheses, 1):
        confidence_pct = int(hyp.confidence * 100)
        expanded = i <= 2
        with st.expander(f"#{i} · {hyp.description}", expanded=expanded):
            st.markdown(
                f"""
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4rem;">
                  <span style="color:var(--aa-muted);font-size:12px;">Confidence</span>
                  <span style="color:var(--aa-text);font-weight:650;font-variant-numeric:tabular-nums;font-size:13px;">{confidence_pct}%</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.progress(min(max(hyp.confidence, 0.0), 1.0))
            st.markdown(
                f"<span style='color:var(--aa-dim);font-size:12px;'>Category: "
                f"<code style='font-family:var(--aa-mono);color:var(--aa-muted);'>{hyp.category}</code> · "
                f"Supporting events: <code style='font-family:var(--aa-mono);color:var(--aa-muted);'>{hyp.supporting_events}</code></span>",
                unsafe_allow_html=True,
            )

            if hyp.suggested_fixes:
                st.markdown(
                    '<div style="color:var(--aa-dim);font:600 10px var(--aa-mono);letter-spacing:.1em;'
                    'text-transform:uppercase;margin:.8rem 0 .35rem;">Suggested fixes</div>',
                    unsafe_allow_html=True,
                )
                for fix in hyp.suggested_fixes:
                    st.markdown(
                        f'<div style="display:flex;gap:.6rem;align-items:flex-start;padding:.32rem 0;'
                        f'color:var(--aa-muted);font-size:12.5px;border-top:1px solid var(--aa-border-soft);">'
                        f'<span style="color:var(--aa-accent);font:600 11px var(--aa-mono);">✓</span>{fix}</div>',
                        unsafe_allow_html=True,
                    )


def render_timeline_tab(trace):
    """Render timeline tab."""
    theme.render_section_header("Sequence", "Event timeline")

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        event_types = ["All"] + list(set(e.type.value for e in trace.events))
        selected_type = st.selectbox("Event Type", event_types)
    with col2:
        show_errors_only = st.checkbox("Errors Only")
    with col3:
        search_term = st.text_input("Search", placeholder="Filter by name or content")

    # Filter events
    events = trace.events
    if selected_type != "All":
        events = [e for e in events if e.type.value == selected_type]
    if show_errors_only:
        events = [e for e in events if e.is_error()]
    if search_term:
        events = [e for e in events if search_term.lower() in str(e.name or "").lower()
                  or search_term.lower() in str(e.input or "").lower()
                  or search_term.lower() in str(e.output or "").lower()]

    st.markdown(
        f'<div style="color:var(--aa-dim);font-size:12px;margin:.35rem 0 .6rem;">'
        f"Showing <b style='color:var(--aa-muted);'>{len(events)}</b> of {len(trace.events)} events</div>",
        unsafe_allow_html=True,
    )

    # Display events
    for event in events[:100]:  # Limit to 100 for performance
        display_name = get_event_display_name(event)
        is_error = event.is_error()
        marker = "❌" if is_error else "·"
        with st.expander(f"{marker} Event {event.event_id}: {event.type.value} — {display_name}"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Type:** {event.type.value}")
                st.markdown(f"**Name:** {event.name or 'N/A'}")
                if event.latency_ms:
                    st.markdown(f"**Latency:** {event.latency_ms}ms")
                if event.token_count:
                    st.markdown(f"**Tokens:** {event.token_count}")
            with col2:
                if event.error:
                    st.error(f"**Error:** {event.error.message}")

            if event.input:
                st.markdown("**Input:**")
                if isinstance(event.input, (dict, list)):
                    st.json(event.input)
                elif isinstance(event.input, str):
                    if len(event.input) > 1000:
                        st.code(event.input[:1000] + "...")
                    else:
                        st.code(event.input)
                else:
                    st.code(str(event.input)[:1000])

            if event.output:
                st.markdown("**Output:**")
                if isinstance(event.output, (dict, list)):
                    st.json(event.output)
                elif isinstance(event.output, str):
                    if len(event.output) > 1000:
                        st.code(event.output[:1000] + "...")
                    else:
                        st.code(event.output)
                else:
                    st.code(str(event.output)[:1000])

    if len(events) > 100:
        st.info(f"Showing first 100 events. {len(events) - 100} more events not shown.")


def render_report_tab():
    """Render report tab."""
    theme.render_section_header("Output", "Generated report")

    if not st.session_state.report_markdown:
        theme.render_empty_state(
            "play",
            "No report yet",
            "Run the analysis pipeline to generate a Markdown and JSON report.",
        )
        return

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download Markdown",
            st.session_state.report_markdown,
            file_name=f"autopsy_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
            width='stretch',
        )
    with col2:
        st.download_button(
            "Download JSON",
            json.dumps(st.session_state.report_json, indent=2, default=str),
            file_name=f"autopsy_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            width='stretch',
        )

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.2rem 0;"></div>',
        unsafe_allow_html=True,
    )

    # Report preview
    with st.expander("Preview report", expanded=True):
        st.markdown(st.session_state.report_markdown)


# ---------------------------------------------------------------------------
# Trace viewer
# ---------------------------------------------------------------------------

def render_trace_viewer_page():
    """Render the trace viewer page."""
    theme.render_hero(
        "Viewer",
        "Walk through the run, event by event.",
        "Browse every event in the loaded trace with full input, output, and error context.",
    )

    if not st.session_state.trace:
        st.info("Load a trace from the Analyze page first, or select one below.")

        traces_dir = Path("traces")
        if traces_dir.exists():
            trace_files = list(traces_dir.glob("*.json"))
            if trace_files:
                selected_file = st.selectbox(
                    "Select trace file",
                    options=trace_files,
                    format_func=lambda x: x.name,
                )
                if st.button("Load Trace", type="primary"):
                    try:
                        st.session_state.trace = api.load_trace(selected_file)
                        st.rerun()
                    except (ParseError, SchemaValidationError, PluginError) as e:
                        st.error(f"Error parsing trace: {e}")
                    except Exception:
                        logger.exception("Failed loading trace in viewer: %s", selected_file)
                        st.error("Error parsing trace (see logs for details).")
            else:
                theme.render_empty_state(
                    "file",
                    "No trace files found",
                    "Place JSON trace exports in ./traces/ to select them here.",
                )
        else:
            theme.render_empty_state(
                "file",
                "Traces directory not found",
                "Create ./traces/ and add JSON exports to use directory selection.",
            )
        return

    trace = st.session_state.trace

    # Two-column layout
    col1, col2 = st.columns([1, 2])

    with col1:
        st.markdown(
            '<div class="aa-section-kicker" style="margin-bottom:.55rem;">Events</div>',
            unsafe_allow_html=True,
        )

        # Filters
        event_types = ["All"] + list(set(e.type.value for e in trace.events))
        selected_type = st.selectbox("Filter by type", event_types, key="viewer_type", label_visibility="collapsed")
        show_errors = st.checkbox("Show errors only", key="viewer_errors")

        # Event list
        events = trace.events
        if selected_type != "All":
            events = [e for e in events if e.type.value == selected_type]
        if show_errors:
            events = [e for e in events if e.is_error()]

        selected_event = None
        for event in events[:50]:
            marker = "❌ " if event.is_error() else ""
            display_name = get_event_display_name(event)
            label = f"{marker}{event.event_id}: {event.type.value[:10]}"
            if display_name != "unnamed":
                label += f" — {display_name[:15]}"
            if st.button(label, key=f"ev_{event.event_id}", width='stretch'):
                selected_event = event

        if not events:
            st.markdown(
                '<div style="color:var(--aa-dim);font-size:12.5px;padding:.6rem 0;">No events match the filters.</div>',
                unsafe_allow_html=True,
            )

    with col2:
        st.markdown(
            '<div class="aa-section-kicker" style="margin-bottom:.55rem;">Details</div>',
            unsafe_allow_html=True,
        )

        if selected_event:
            event = selected_event

            st.markdown(f"### Event {event.event_id}: {event.type.value}")

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Name:** {event.name or 'N/A'}")
                st.markdown(f"**Role:** {event.role.value if event.role else 'N/A'}")
            with col_b:
                st.markdown(f"**Latency:** {event.latency_ms}ms" if event.latency_ms else "**Latency:** N/A")
                st.markdown(f"**Tokens:** {event.token_count}" if event.token_count else "**Tokens:** N/A")

            if event.error:
                error_msg = event.error.message
                if event.error.category:
                    error_msg = f"{event.error.category}: {error_msg}"
                st.error(f"**Error:** {error_msg}")
                if event.error.stack:
                    with st.expander("Stack Trace"):
                        st.code(event.error.stack)

            st.markdown(
                '<div style="height:1px;background:var(--aa-border-soft);margin:1rem 0;"></div>',
                unsafe_allow_html=True,
            )

            st.markdown("**Input:**")
            if event.input:
                if isinstance(event.input, (dict, list)):
                    st.json(event.input)
                elif isinstance(event.input, str):
                    st.code(event.input)
                else:
                    st.code(str(event.input))
            else:
                st.markdown(
                    '<div style="color:var(--aa-dim);font-size:12.5px;">No input</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("**Output:**")
            if event.output:
                if isinstance(event.output, (dict, list)):
                    st.json(event.output)
                elif isinstance(event.output, str):
                    st.code(event.output)
                else:
                    st.code(str(event.output))
            else:
                st.markdown(
                    '<div style="color:var(--aa-dim);font-size:12.5px;">No output</div>',
                    unsafe_allow_html=True,
                )
        else:
            theme.render_empty_state(
                "search",
                "Select an event",
                "Pick an event from the left panel to inspect its input, output, and metadata.",
            )


# ---------------------------------------------------------------------------
# Batch analysis
# ---------------------------------------------------------------------------

def render_batch_analysis_page():
    """Render the batch analysis page."""
    theme.render_hero(
        "Batch",
        "Analyze every trace in a directory.",
        "Run the deterministic pipeline across all JSON traces in a folder and review the "
        "results in a single table.",
    )

    # Directory selection
    default_traces_dir = "./traces"
    traces_dir = st.text_input("Traces Directory", value=default_traces_dir)

    # Options
    col1, col2 = st.columns(2)
    with col1:
        no_llm = st.checkbox("Deterministic Only (faster)", value=True, key="batch_no_llm")
    with col2:
        save_reports = st.checkbox("Save Individual Reports", value=True)

    traces_path = Path(traces_dir)
    if traces_path.exists():
        trace_files = list(traces_path.glob("*.json"))
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:.5rem;margin:.5rem 0;">'
            f'<span class="aa-badge tone-info"><i></i>{len(trace_files)} trace files</span></div>',
            unsafe_allow_html=True,
        )
    else:
        trace_files = []
        st.warning("Directory not found")

    # Run batch analysis
    if st.button("Run Batch Analysis", type="primary", disabled=len(trace_files) == 0, width='stretch'):
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        for i, trace_file in enumerate(trace_files):
            status_text.text(f"Analyzing {trace_file.name}...")

            try:
                trace = api.load_trace(trace_file)

                preanalysis = api.run_preanalysis(trace)
                llm_fallback_error = None

                if no_llm:
                    result = api.run_deterministic_analysis(trace)
                else:
                    try:
                        result = api.run_llm_analysis(trace)
                    except Exception as e:
                        llm_fallback_error = str(e)
                        logger.exception(
                            "LLM analysis failed for batch trace %s. Falling back to deterministic mode.",
                            trace_file,
                        )
                        result = api.run_deterministic_analysis(trace)

                # Save report if requested
                report_path = None
                if save_reports:
                    reports_dir = Path("reports")
                    reports_dir.mkdir(exist_ok=True)
                    report_gen = api.generate_report(trace, result)
                    report_path = report_gen.save(reports_dir / f"{trace_file.stem}.md")

                results.append({
                    "file": trace_file.name,
                    "run_id": trace.run_id,
                    "status": trace.status.value,
                    "events": len(trace.events),
                    "errors": trace.stats.num_errors,
                    "signals": len(preanalysis.signals),
                    "hypotheses": len(preanalysis.hypotheses),
                    "success": True,
                    "llm_fallback_error": llm_fallback_error,
                    "report_path": str(report_path) if report_path else None,
                })

            except Exception as e:
                logger.exception("Batch analysis failed for trace file: %s", trace_file)
                results.append({
                    "file": trace_file.name,
                    "status": "ERROR",
                    "error": str(e),
                    "success": False,
                })

            progress_bar.progress((i + 1) / len(trace_files))

        status_text.text("Batch analysis complete!")
        st.session_state.batch_results = results

    # Display results
    if st.session_state.batch_results:
        st.markdown(
            '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
            unsafe_allow_html=True,
        )
        theme.render_section_header("Output", "Results")

        results = st.session_state.batch_results

        # Summary metrics
        successful = sum(1 for r in results if r.get("success"))
        total_signals = sum(r.get("signals", 0) for r in results)
        total_errors = sum(r.get("errors", 0) for r in results)
        theme.render_stat_cards(
            [
                {"label": "Total traces", "value": len(results)},
                {"label": "Successful", "value": successful, "tone": "success"},
                {"label": "Total signals", "value": total_signals, "tone": "info"},
                {"label": "Total errors", "value": total_errors, "tone": "error" if total_errors else None},
            ],
            columns=4,
        )

        # Results table
        st.dataframe(
            results,
            width='stretch',
            column_config={
                "file": "File",
                "run_id": "Run ID",
                "status": "Status",
                "events": "Events",
                "errors": "Errors",
                "signals": "Signals",
                "hypotheses": "Hypotheses",
                "success": "Success",
            },
        )

        fallback_rows = [r for r in results if r.get("llm_fallback_error")]
        if fallback_rows:
            st.warning(f"LLM fallback used for {len(fallback_rows)} trace(s).")
            with st.expander("LLM fallback details"):
                for row in fallback_rows:
                    st.markdown(f"- **{row['file']}**: {row['llm_fallback_error']}")

        error_rows = [r for r in results if not r.get("success")]
        if error_rows:
            st.error(f"{len(error_rows)} trace(s) failed during batch analysis.")
            with st.expander("Batch analysis errors"):
                for row in error_rows:
                    st.markdown(f"- **{row['file']}**: {row.get('error', 'Unknown error')}")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def render_reports_page():
    """Render the reports page."""
    theme.render_hero(
        "Reports",
        "Browse generated reports.",
        "All analysis outputs are stored on disk as Markdown or JSON and listed here "
        "newest first.",
    )

    reports_dir = Path("reports")

    if not reports_dir.exists():
        theme.render_empty_state(
            "file",
            "No reports directory found",
            "Run an analysis to generate reports — they will be stored in ./reports/.",
        )
        return

    # List report files
    report_files = list(reports_dir.glob("*.md")) + list(reports_dir.glob("*.json"))
    report_files = sorted(report_files, key=lambda x: x.stat().st_mtime, reverse=True)

    if not report_files:
        theme.render_empty_state(
            "file",
            "No reports found",
            "Run an analysis to generate reports — they will appear here.",
        )
        return

    # Report list
    col1, col2 = st.columns([1, 2])

    with col1:
        selected_report = None
        for idx, report_file in enumerate(report_files[:20]):
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime)
            label = f"{report_file.name}\n{mtime.strftime('%Y-%m-%d %H:%M')}"
            if st.button(label, key=f"rep_{idx}_{report_file.name}", width='stretch'):
                selected_report = report_file

    with col2:
        if selected_report:
            content = selected_report.read_text()

            # Download button
            st.download_button(
                "Download Report",
                content,
                file_name=selected_report.name,
                mime="text/markdown" if selected_report.suffix == ".md" else "application/json",
            )

            st.markdown(
                '<div style="height:1px;background:var(--aa-border-soft);margin:1.1rem 0;"></div>',
                unsafe_allow_html=True,
            )

            with st.expander("Preview report", expanded=True):
                if selected_report.suffix == ".json":
                    try:
                        st.json(json.loads(content))
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON report content in %s", selected_report, exc_info=True)
                        st.code(content)
                else:
                    st.markdown(content)
        else:
            theme.render_empty_state(
                "search",
                "Select a report",
                "Choose a report from the list on the left to preview and download it.",
            )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def render_settings_page():
    """Render the settings page."""
    theme.render_hero(
        "Settings",
        "Configuration",
        "Review the current pipeline configuration, credential status, and detection "
        "thresholds.",
    )

    config = get_config()

    # API Key status
    if api.llm_credentials_configured(config):
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:.6rem;border:1px solid rgba(103,189,145,.3);'
            f'background:rgba(103,189,145,.07);border-radius:8px;padding:.85rem 1rem;">'
            f'{theme.status_badge("success", "Configured")}'
            f'<span style="color:var(--aa-muted);font-size:13px;">LLM credentials are configured for provider '
            f'<code style="font-family:var(--aa-mono);color:var(--aa-text);">{config.llm_provider}</code></span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.warning(f"LLM credentials are not set for provider: {config.llm_provider}")
        st.markdown("""
        Configure API keys in `.env` (e.g. `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)
        and set `PROVIDER` / `LLM_PROVIDER` to match. See project README.
        """)

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
        unsafe_allow_html=True,
    )

    theme.render_section_header("Models", "Model settings")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f'<div class="aa-card"><div class="aa-stat-label">Default model</div>'
            f'<div class="aa-stat-value" style="font-size:17px;">{config.default_model}</div></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f'<div class="aa-card"><div class="aa-stat-label">Fallback model</div>'
            f'<div class="aa-stat-value" style="font-size:17px;">{config.fallback_model}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
        unsafe_allow_html=True,
    )

    theme.render_section_header("Detection", "Thresholds")
    theme.render_stat_cards(
        [
            {"label": "Loop threshold", "value": config.loop_threshold},
            {"label": "Context overflow", "value": f"{config.context_overflow_threshold} tokens"},
            {"label": "Retry window", "value": f"{config.retry_window_seconds}s"},
            {"label": "Max retries", "value": config.max_retries},
        ],
        columns=4,
    )

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
        unsafe_allow_html=True,
    )

    theme.render_section_header("Paths", "Directories")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f'<div class="aa-card"><div class="aa-stat-label">Output directory</div>'
            f'<div class="aa-stat-value" style="font-size:15px;font-family:var(--aa-mono);word-break:break-all;">{config.output_dir}</div></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f'<div class="aa-card"><div class="aa-stat-label">Trace directory</div>'
            f'<div class="aa-stat-value" style="font-size:15px;font-family:var(--aa-mono);word-break:break-all;">{config.trace_dir}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="height:1px;background:var(--aa-border-soft);margin:1.4rem 0;"></div>',
        unsafe_allow_html=True,
    )

    with st.expander("View full configuration (JSON)"):
        st.json(config.to_dict())


# Entry scripts: `app.py` (home) and `pages/*.py` call :func:`configure_page`,
# :func:`init_session_state`, :func:`render_sidebar`, and the appropriate ``render_*`` function.
