"""Tests for CLI gating flags: --fail-on (analyze) and --fail-on-regression (diff)."""

from pathlib import Path

from typer.testing import CliRunner

from src.cli import app


runner = CliRunner()
LOOP = "examples/traces/loop_failure.json"
SUCCESS = "examples/traces/successful_run.json"


def test_analyze_fail_on_matching_pattern_exits_1():
    result = runner.invoke(
        app,
        ["analyze", LOOP, "--no-llm", "--no-embeddings", "-q", "--fail-on", "infinite_loop", "-f", "json"],
    )
    assert result.exit_code == 1


def test_analyze_fail_on_non_matching_pattern_exits_0():
    # token_waste is not detected in loop_failure.json fixture
    result = runner.invoke(
        app,
        ["analyze", LOOP, "--no-llm", "--no-embeddings", "-q", "--fail-on", "token_waste", "-f", "json"],
    )
    assert result.exit_code == 0


def test_analyze_no_fail_on_still_uses_default_gate():
    # Without --fail-on, the existing 'fail on any finding' behavior applies
    result = runner.invoke(
        app,
        ["analyze", LOOP, "--no-llm", "--no-embeddings", "-q", "-f", "json"],
    )
    assert result.exit_code == 1


def test_analyze_fail_on_clean_trace_exits_0():
    result = runner.invoke(
        app,
        ["analyze", SUCCESS, "--no-llm", "--no-embeddings", "-q", "--fail-on", "infinite_loop", "-f", "json"],
    )
    assert result.exit_code == 0


def test_diff_fail_on_regression_when_b_has_new_pattern():
    # successful_run -> loop_failure should regress (loop pattern appears in B only)
    result = runner.invoke(
        app,
        ["diff", SUCCESS, LOOP, "--fail-on-regression", "-f", "json"],
    )
    assert result.exit_code == 1


def test_diff_fail_on_regression_when_no_change():
    result = runner.invoke(
        app,
        ["diff", SUCCESS, SUCCESS, "--fail-on-regression", "-f", "json"],
    )
    assert result.exit_code == 0


def test_diff_without_flag_returns_0_even_on_regression():
    result = runner.invoke(
        app,
        ["diff", SUCCESS, LOOP, "-f", "json"],
    )
    assert result.exit_code == 0
