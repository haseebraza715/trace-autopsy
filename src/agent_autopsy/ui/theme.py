"""Agent Autopsy design system.

A single source of truth for the Streamlit UI's look and feel. Everything is
self-contained (system font stacks, no external requests) and mirrors the
dark, terminal-inspired aesthetic of the focused demo page.

Conventions
-----------
* Semantic color tokens (``--aa-*``) with a 4px spacing grid.
* Border-based cards (no heavy shadows) with a hairline divider pattern.
* 3-tier typography scale: mono eyebrow labels, tight display values
  (tabular-nums), 14px body.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from typing import Any

import streamlit as st

SEVERITY_TONES: dict[str, dict[str, str]] = {
    "critical": {"bg": "#ef6a6a", "fg": "#180a0a", "label": "Critical"},
    "high": {"bg": "rgba(230,170,74,.14)", "fg": "#e0a04f", "label": "High"},
    "medium": {"bg": "rgba(110,168,220,.14)", "fg": "#7fb2dd", "label": "Medium"},
    "low": {"bg": "rgba(148,158,168,.12)", "fg": "#98a2ad", "label": "Low"},
}

STATUS_TONES: dict[str, dict[str, str]] = {
    "success": {"bg": "rgba(103,189,145,.14)", "fg": "#67bd91"},
    "warning": {"bg": "rgba(230,170,74,.14)", "fg": "#e0a04f"},
    "error": {"bg": "rgba(239,106,106,.14)", "fg": "#ef6a6a"},
    "info": {"bg": "rgba(110,168,220,.14)", "fg": "#7fb2dd"},
    "neutral": {"bg": "rgba(148,158,168,.10)", "fg": "#98a2ad"},
}

_GLOBAL_CSS = """
<style>
:root {
  --aa-bg: #0a0d10;
  --aa-surface: #101418;
  --aa-surface-2: #151a1f;
  --aa-surface-3: #1a2026;
  --aa-border: #262d35;
  --aa-border-soft: #1c2228;
  --aa-text: #f2f0e9;
  --aa-muted: #9aa3ad;
  --aa-dim: #67707a;
  --aa-accent: #e6aa4a;
  --aa-accent-hover: #f0b65b;
  --aa-accent-ink: #17120a;
  --aa-red: #ef6a6a;
  --aa-amber: #e0a04f;
  --aa-blue: #7fb2dd;
  --aa-green: #67bd91;
  --aa-radius: 8px;
  --aa-radius-sm: 5px;
  --aa-font: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --aa-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--aa-bg);
  color: var(--aa-text);
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { visibility: hidden; height: 0; }

.block-container {
  max-width: 1280px !important;
  padding: 2.2rem 2.4rem 4.5rem !important;
}

.stApp, button, input, textarea, select {
  font-family: var(--aa-font);
}

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] {
  background: var(--aa-surface);
  border-right: 1px solid var(--aa-border-soft);
}
[data-testid="stSidebar"] > div { padding: 1.1rem 0.9rem; }
[data-testid="stSidebar"] * { color: var(--aa-text); }
[data-testid="stSidebar"] hr { border-color: var(--aa-border-soft); }

.sidebar-brand {
  display: flex; align-items: center; gap: .7rem;
  padding: .15rem .4rem .9rem;
}
.sidebar-brand-mark {
  width: 30px; height: 30px; display: grid; place-items: center;
  border: 1px solid #5c492b; background: #17130d; color: var(--aa-accent);
  font: 600 13px var(--aa-mono); border-radius: var(--aa-radius-sm);
}
.sidebar-brand-name { font-size: 15px; font-weight: 650; letter-spacing: -.01em; }
.sidebar-brand-sub { color: var(--aa-dim); font: 500 10px var(--aa-mono); letter-spacing: .05em; text-transform: uppercase; }

.sidebar-heading {
  color: var(--aa-dim); font: 600 10px var(--aa-mono);
  letter-spacing: .12em; text-transform: uppercase;
  margin: .9rem .4rem .45rem;
}

/* Page links */
[data-testid="stSidebarNav"] { background: transparent; padding: .4rem .5rem; }
[data-testid="stSidebarNav"] ul { gap: 2px; }
[data-testid="stSidebarNav"] a {
  color: var(--aa-muted); border-radius: var(--aa-radius-sm);
  padding: .5rem .6rem !important; font-size: 13.5px; font-weight: 500;
}
[data-testid="stSidebarNav"] a:hover { background: var(--aa-surface-2); color: var(--aa-text); }
[data-testid="stSidebarNav"] a[aria-current="page"],
[data-testid="stSidebarNav"] a:focus { background: rgba(230,170,74,.1); color: var(--aa-accent); }
[data-testid="stSidebarNav"] a[aria-current="page"] svg,
[data-testid="stSidebarNav"] a:focus svg { fill: var(--aa-accent); }
[data-testid="stSidebarNav"] a span:empty { display: none; }

.stPageLink a {
  color: var(--aa-muted) !important; border-radius: var(--aa-radius-sm);
  padding: .45rem .6rem !important; font-size: 13.5px; font-weight: 500;
  border: 1px solid transparent;
}
.stPageLink a:hover { background: var(--aa-surface-2); color: var(--aa-text) !important; }

/* ---------- Typography ---------- */
.aa-eyebrow {
  color: var(--aa-accent); font: 600 10px var(--aa-mono);
  letter-spacing: .14em; text-transform: uppercase; margin: 0 0 .55rem;
}
.aa-hero-title {
  color: var(--aa-text); font-size: 30px; font-weight: 620;
  letter-spacing: -.03em; line-height: 1.15; margin: 0 0 .6rem;
}
.aa-hero-sub {
  color: var(--aa-muted); font-size: 14px; line-height: 1.6;
  margin: 0; max-width: 720px;
}
.aa-section-title { font-size: 17px; font-weight: 620; letter-spacing: -.02em; color: var(--aa-text); margin: 0; }
.aa-section-kicker { color: var(--aa-accent); font: 600 10px var(--aa-mono); letter-spacing: .12em; text-transform: uppercase; }
.aa-muted { color: var(--aa-muted); }

/* ---------- Cards ---------- */
.aa-card {
  border: 1px solid var(--aa-border);
  background: var(--aa-surface);
  border-radius: var(--aa-radius);
  padding: 1.1rem 1.2rem;
}
.aa-card + .aa-card { margin-top: .9rem; }
.aa-card-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: .65rem; }

/* ---------- Stat cards ---------- */
.aa-stat-grid { display: grid; gap: .85rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-bottom: 1.4rem; }
.aa-stat {
  border: 1px solid var(--aa-border); background: var(--aa-surface);
  border-radius: var(--aa-radius); padding: .95rem 1.05rem .9rem;
}
.aa-stat-label {
  color: var(--aa-dim); font: 600 9px var(--aa-mono);
  letter-spacing: .1em; text-transform: uppercase;
}
.aa-stat-value {
  color: var(--aa-text); font-size: 25px; font-weight: 630;
  letter-spacing: -.02em; font-variant-numeric: tabular-nums;
  margin: .3rem 0 .15rem; line-height: 1.05;
}
.aa-stat-hint { color: var(--aa-dim); font-size: 11.5px; }
.aa-stat.tone-accent { border-color: rgba(230,170,74,.35); }
.aa-stat.tone-accent .aa-stat-value { color: var(--aa-accent); }
.aa-stat.tone-error { border-color: rgba(239,106,106,.3); }
.aa-stat.tone-error .aa-stat-value { color: var(--aa-red); }
.aa-stat.tone-success { border-color: rgba(103,189,145,.3); }
.aa-stat.tone-success .aa-stat-value { color: var(--aa-green); }
.aa-stat.tone-info { border-color: rgba(110,168,220,.3); }
.aa-stat.tone-info .aa-stat-value { color: var(--aa-blue); }

/* ---------- Badges & pills ---------- */
.aa-badge {
  display: inline-flex; align-items: center; gap: .4rem;
  border-radius: 999px; padding: .22rem .55rem;
  font: 600 10px var(--aa-mono); letter-spacing: .05em; text-transform: uppercase;
  white-space: nowrap;
}
.aa-badge i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.aa-badge.tone-critical { background: #ef6a6a; color: #180a0a; }
.aa-badge.tone-critical i { background: #180a0a; }
.aa-badge.tone-success { background: rgba(103,189,145,.14); color: #67bd91; }
.aa-badge.tone-warning { background: rgba(230,170,74,.14); color: #e0a04f; }
.aa-badge.tone-error { background: rgba(239,106,106,.14); color: #ef6a6a; }
.aa-badge.tone-info { background: rgba(110,168,220,.14); color: #7fb2dd; }
.aa-badge.tone-neutral { background: rgba(148,158,168,.1); color: #98a2ad; }

/* ---------- Empty states ---------- */
.aa-empty {
  text-align: center; border: 1px dashed var(--aa-border);
  background: var(--aa-surface); border-radius: var(--aa-radius);
  padding: 2.6rem 1.6rem; margin: 1.2rem 0;
}
.aa-empty-icon {
  width: 44px; height: 44px; margin: 0 auto .95rem; display: grid; place-items: center;
  border: 1px solid var(--aa-border); border-radius: 10px;
  background: var(--aa-surface-2); color: var(--aa-accent); font-size: 19px;
}
.aa-empty h3 { color: var(--aa-text); font-size: 15.5px; font-weight: 620; letter-spacing: -.01em; margin: 0 0 .35rem; }
.aa-empty p { color: var(--aa-muted); font-size: 13px; line-height: 1.6; margin: 0 auto; max-width: 460px; }

/* ---------- Buttons ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  border-radius: var(--aa-radius-sm); font-weight: 600;
  letter-spacing: -.01em; min-height: 40px;
  border: 1px solid var(--aa-border);
  background: var(--aa-surface-2); color: var(--aa-text);
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover { border-color: #39424d; background: var(--aa-surface-3); color: var(--aa-text); }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: var(--aa-accent); color: var(--aa-accent-ink);
  border: 1px solid var(--aa-accent);
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {
  background: var(--aa-accent-hover); border-color: var(--aa-accent-hover); color: var(--aa-accent-ink);
}
.stButton > button:disabled, .stDownloadButton > button:disabled { opacity: .45; }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(230,170,74,.3); }

/* ---------- Tabs ---------- */
[data-testid="stTabs"] { gap: .4rem; margin-top: .35rem; }
[data-testid="stTabs"] button {
  border-radius: var(--aa-radius-sm); color: var(--aa-dim);
  font-size: 13.5px; font-weight: 550; padding: .45rem .85rem;
  background: transparent; border-bottom: 2px solid transparent;
}
[data-testid="stTabs"] button:hover { color: var(--aa-text); background: var(--aa-surface-2); }
[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--aa-accent); background: rgba(230,170,74,.08);
  border-bottom-color: var(--aa-accent);
}

/* ---------- Expanders ---------- */
[data-testid="stExpander"] {
  border: 1px solid var(--aa-border) !important;
  border-radius: var(--aa-radius) !important;
  background: var(--aa-surface); margin-top: .7rem; overflow: hidden;
}
[data-testid="stExpander"] summary {
  color: var(--aa-text); font-size: 13.5px; font-weight: 550;
  border-radius: var(--aa-radius);
}
[data-testid="stExpander"] summary:hover { background: var(--aa-surface-2); }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { border-top: 1px solid var(--aa-border-soft); padding-top: .75rem; }

/* ---------- Inputs ---------- */
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea, [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stSelectbox"] > div > div, [data-testid="stMultiSelect"] > div > div,
[data-testid="stTextArea"] div[data-baseweb="textarea"] {
  background: var(--aa-surface-2) !important; border-color: var(--aa-border) !important;
  color: var(--aa-text) !important; border-radius: var(--aa-radius-sm) !important;
  caret-color: var(--aa-accent);
}
[data-testid="stTextInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder { color: var(--aa-dim); }
[data-testid="stTextInput"] label, [data-testid="stTextArea"] label,
[data-testid="stSelectbox"] label, [data-testid="stMultiSelect"] label,
[data-testid="stNumberInput"] label, [data-testid="stCheckbox"] label,
[data-testid="stToggle"] label, [data-testid="stRadio"] label, [data-testid="stFileUploader"] label {
  color: var(--aa-muted) !important; font-size: 13px; font-weight: 550;
}
[data-testid="stSelectbox"] [data-baseweb="select"] * { color: var(--aa-text) !important; }
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
  background: rgba(230,170,74,.12) !important; border-radius: 4px;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span { color: var(--aa-accent) !important; }

/* Checkbox & toggle */
[data-testid="stCheckbox"] [data-testid="stCheckbox"] { accent-color: var(--aa-accent); }
[data-testid="stCheckbox"] div[role="checkbox"], [data-testid="stToggle"] div[role="switch"] { background: var(--aa-surface-3); }
[data-testid="stCheckbox"] div[role="checkbox"][aria-checked="true"], [data-testid="stToggle"] div[role="switch"][aria-checked="true"] { background: var(--aa-accent); }
[data-testid="stCheckbox"] div[role="checkbox"][aria-checked="true"] svg, [data-testid="stToggle"] div[role="switch"][aria-checked="true"] span { color: var(--aa-accent-ink) !important; }

/* Sliders */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] { background: var(--aa-accent); border-color: var(--aa-accent); }
[data-testid="stSlider"] [data-testid="stTickBar"] div { color: var(--aa-dim); }

/* File uploader */
[data-testid="stFileUploaderDropzone"] {
  background: var(--aa-surface-2); border: 1px dashed #39424d;
  border-radius: var(--aa-radius-sm);
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--aa-accent); }
[data-testid="stFileUploaderDropzoneInstructions"] span { color: var(--aa-muted); font-size: 13px; }
[data-testid="stFileUploaderDropzoneInstructions"] small { color: var(--aa-dim); }

/* ---------- Alerts ---------- */
[data-testid="stAlert"] { border-radius: var(--aa-radius); border: 1px solid; }
[data-testid="stAlert"][data-baseweb="notification"] { background: var(--aa-surface); }
[data-testid="stAlert"] [data-testid="stNotificationContent"] { color: var(--aa-text); }
[data-testid="stAlert"] svg { color: var(--aa-accent); }

/* ---------- Metrics (native fallback) ---------- */
[data-testid="stMetric"] {
  background: var(--aa-surface); border: 1px solid var(--aa-border);
  border-radius: var(--aa-radius); padding: .9rem 1rem;
}
[data-testid="stMetric"] label { color: var(--aa-dim); font: 600 9px var(--aa-mono); letter-spacing: .1em; text-transform: uppercase; }
[data-testid="stMetric"] [data-testid="stMetricValue"] { color: var(--aa-text); font-weight: 630; font-size: 25px; font-variant-numeric: tabular-nums; }
[data-testid="stMetric"] [data-testid="stMetricDelta"] { color: var(--aa-muted); }

/* ---------- Code blocks ---------- */
[data-testid="stCode"] { border-radius: var(--aa-radius); border: 1px solid var(--aa-border); }
[data-testid="stCode"] code { font-family: var(--aa-mono); font-size: 12px; }

/* ---------- DataFrames ---------- */
[data-testid="stDataFrame"] { border-radius: var(--aa-radius); border: 1px solid var(--aa-border); overflow: hidden; }
[data-testid="stDataFrame"] [data-testid="stElementContainer"] { font-size: 12.5px; }

/* ---------- Dividers ---------- */
hr { border-color: var(--aa-border-soft) !important; margin: 1.3rem 0 !important; }

/* ---------- Status / progress ---------- */
[data-testid="stProgress"] > div > div { background: rgba(230,170,74,.15); }
[data-testid="stProgress"] > div > div > div { background: var(--aa-accent); }
[data-testid="stSpinner"] { color: var(--aa-muted); }
[data-testid="stSpinner"] div { border-top-color: var(--aa-accent) !important; }

/* ---------- Tables ---------- */
[data-testid="stTable"] { border-radius: var(--aa-radius); border: 1px solid var(--aa-border); }
[data-testid="stTable"] td { color: var(--aa-muted); }

/* ---------- Captions ---------- */
[data-testid="stCaptionContainer"] p, .stCaption { color: var(--aa-dim) !important; font-size: 12px; }

/* ---------- Info / Success / Warning / Error text ---------- */
[data-testid="stMarkdownContainer"] .stAlert { color: var(--aa-text); }

/* ---------- Container borders ---------- */
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--aa-border) !important; border-radius: var(--aa-radius) !important; }

@media (max-width: 800px) {
  .block-container { padding: 1.4rem 1.1rem 3rem !important; }
  .aa-hero-title { font-size: 24px; }
}
</style>
"""

_EMPTY_ICONS = {
    "search": "◉",
    "upload": "⇪",
    "file": "▤",
    "play": "▶",
    "gear": "⚙",
    "chart": "▥",
    "check": "✓",
}


def inject_global_styles() -> None:
    """Apply the global design system. Call once per page, after set_page_config."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def render_hero(eyebrow: str, title: str, subtitle: str) -> None:
    """Page-level hero: eyebrow, large tight title, muted supporting copy."""
    st.markdown(
        f"""
        <div style="margin-bottom:1.6rem;">
          <div class="aa-eyebrow">{html.escape(eyebrow)}</div>
          <h1 class="aa-hero-title">{html.escape(title)}</h1>
          <p class="aa-hero-sub">{html.escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_header(kicker: str, title: str, right: str | None = None) -> None:
    """Section header with optional right-aligned HTML (e.g. a badge)."""
    right_html = f'<span>{right}</span>' if right else ""
    st.markdown(
        f"""
        <div class="aa-card-head" style="margin:1.2rem 0 .8rem;">
          <div>
            <div class="aa-section-kicker">{html.escape(kicker)}</div>
            <div class="aa-section-title">{html.escape(title)}</div>
          </div>
          {right_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_cards(items: list[dict[str, Any]], columns: int | None = None) -> None:
    """Grid of stat cards.

    Each item: ``{"label", "value", "hint", "tone"}`` where tone is one of
    ``accent | error | success | info | None``.
    """
    if not items:
        return
    styles = []
    for item in items:
        tone = f" tone-{item.get('tone')}" if item.get("tone") else ""
        hint = item.get("hint")
        hint_html = f'<div class="aa-stat-hint">{html.escape(str(hint))}</div>' if hint else ""
        styles.append(
            f"""
            <div class="aa-stat{tone}">
              <div class="aa-stat-label">{html.escape(str(item.get("label", "")))}</div>
              <div class="aa-stat-value">{html.escape(str(item.get("value", "")))}</div>
              {hint_html}
            </div>
            """
        )
    if columns:
        style = f"grid-template-columns:repeat({columns}, 1fr);"
    else:
        style = ""
    st.markdown(
        f'<div class="aa-stat-grid" style="{style}">{"".join(styles)}</div>',
        unsafe_allow_html=True,
    )


def severity_badge(severity: str) -> str:
    """HTML for a severity pill with a leading dot."""
    key = str(severity).lower()
    tone = SEVERITY_TONES.get(key, SEVERITY_TONES["low"])
    return (
        f'<span class="aa-badge tone-{key if key in SEVERITY_TONES else "low"}">'
        f"<i></i>{html.escape(tone['label'])}</span>"
    )


def status_badge(kind: str, label: str | None = None) -> str:
    """HTML for a status pill (success / warning / error / info / neutral)."""
    key = str(kind).lower()
    if key not in STATUS_TONES:
        key = "neutral"
    return (
        f'<span class="aa-badge tone-{key}">'
        f"<i></i>{html.escape(label or key)}</span>"
    )


def render_empty_state(
    icon: str,
    title: str,
    body: str,
    *,
    tone: str = "neutral",
) -> None:
    """Blankslate-style empty state: icon, headline, supporting copy."""
    glyph = _EMPTY_ICONS.get(icon, icon)
    st.markdown(
        f"""
        <div class="aa-empty">
          <div class="aa-empty-icon">{html.escape(glyph)}</div>
          <h3>{html.escape(title)}</h3>
          <p>{html.escape(body)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stepper(steps: list[str], current: int) -> None:
    """Horizontal stepper. ``current`` is the index of the active step."""
    rows = []
    for idx, label in enumerate(steps):
        if idx < current:
            state, marker = "done", "✓"
        elif idx == current:
            state, marker = "active", str(idx + 1)
        else:
            state, marker = "pending", str(idx + 1)
        rows.append(
            f'<div class="aa-step {state}"><span>{marker}</span><em>{html.escape(label)}</em></div>'
        )
    st.markdown(
        f"""
        <style>
        .aa-stepper {{
          display: flex; align-items: center; gap: .35rem;
          border: 1px solid var(--aa-border); background: var(--aa-surface);
          border-radius: var(--aa-radius); padding: .8rem 1rem; margin-bottom: 1.2rem;
        }}
        .aa-step {{ display: flex; align-items: center; gap: .5rem; flex: 1; min-width: 0; }}
        .aa-step span {{
          width: 24px; height: 24px; flex: none; display: grid; place-items: center;
          border-radius: 50%; border: 1px solid var(--aa-border); background: var(--aa-surface-2);
          color: var(--aa-dim); font: 600 11px var(--aa-mono);
        }}
        .aa-step em {{ font-style: normal; color: var(--aa-dim); font-size: 12.5px; font-weight: 550; white-space: nowrap; }}
        .aa-step.active span {{ border-color: var(--aa-accent); color: var(--aa-accent); box-shadow: 0 0 0 4px rgba(230,170,74,.12); }}
        .aa-step.active em {{ color: var(--aa-text); }}
        .aa-step.done span {{ border-color: #3f5c4c; background: rgba(103,189,145,.12); color: var(--aa-green); }}
        .aa-step.done em {{ color: var(--aa-muted); }}
        .aa-step-sep {{ height: 1px; flex: 1; min-width: 12px; background: var(--aa-border); }}
        @media (max-width: 700px) {{ .aa-step em {{ display: none; }} }}
        </style>
        <div class="aa-stepper">
          {rows[0]}
          {"".join(f'<div class="aa-step-sep"></div>{row}' for row in rows[1:])}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_trace_summary_card(
    run_id: str,
    status: str,
    events: int,
    extra: Iterable[tuple[str, str]] = (),
) -> None:
    """Compact card used in the sidebar / trace pages for a loaded trace."""
    badge = status_badge("success" if status.lower() in {"success", "complete"} else "neutral", status)
    extra_rows = "".join(
        f'<div class="aa-sidebar-row"><span>{html.escape(str(k))}</span><b>{html.escape(str(v))}</b></div>'
        for k, v in extra
    )
    st.markdown(
        f"""
        <div class="aa-card" style="padding:.8rem .9rem;">
          <div class="aa-sidebar-title">Loaded trace</div>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:.6rem;margin:.35rem 0 .55rem;">
            <code style="color:var(--aa-muted);font:500 11px var(--aa-mono);word-break:break-all;">{html.escape(run_id[:24])}</code>
            {badge}
          </div>
          <div class="aa-sidebar-row"><span>Events</span><b>{events}</b></div>
          {extra_rows}
        </div>
        <style>
        .aa-sidebar-title {{ color: var(--aa-dim); font: 600 9px var(--aa-mono); letter-spacing: .1em; text-transform: uppercase; }}
        .aa-sidebar-row {{ display: flex; justify-content: space-between; gap: .6rem; padding: .22rem 0; }}
        .aa-sidebar-row span {{ color: var(--aa-dim); font-size: 12px; }}
        .aa-sidebar-row b {{ color: var(--aa-muted); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
