"""Values from the reports index are escaped before reaching unsafe_allow_html."""

from __future__ import annotations

from agent_autopsy.ui.streamlit_pages import _recent_report_markup

PAYLOAD = '<img src=x onerror="alert(1)">'


def test_recent_report_run_id_is_escaped() -> None:
    markup = _recent_report_markup({"run_id": PAYLOAD, "generated_at": "2026-01-01T00:00:00"})

    assert "<img" not in markup["run_id"]
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in markup["run_id"]


def test_recent_report_other_fields_are_escaped() -> None:
    markup = _recent_report_markup(
        {
            "run_id": "ok",
            "generated_at": "<b>x</b>",
            "signals": "<i>",
            "hypotheses": "<u>",
            "status": PAYLOAD,
        }
    )

    for fragment in markup.values():
        assert "<img" not in fragment
        assert "<b>" not in fragment
        assert "<u>" not in fragment
    assert "&lt;i&gt;" in markup["counts"]
    assert "&lt;img" in markup["status"]
