"""CLI command coverage: analyze/validate/summary/diff/fixes/agent-flow/telemetry/benchmark.

Runs the real CLI as a subprocess (offline, deterministic-only) against
small fixture traces.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples" / "traces"
SAMPLE = REPO_ROOT / "tests" / "sample_traces"


def _run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_autopsy.cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestAnalyzeCommand:
    def test_analyze_clean_trace_exits_zero(self) -> None:
        proc = _run("analyze", str(SAMPLE / "successful_run.json"), "--no-llm", "--no-embeddings", "-q")
        assert proc.returncode == 0, proc.stderr + proc.stdout

    def test_analyze_loop_trace_exits_one(self) -> None:
        proc = _run("analyze", str(SAMPLE / "loop_failure.json"), "--no-llm", "--no-embeddings", "-q")
        assert proc.returncode == 1, proc.stderr + proc.stdout

    def test_analyze_writes_output_file(self, tmp_path: Path) -> None:
        out = tmp_path / "report.md"
        proc = _run(
            "analyze",
            str(SAMPLE / "successful_run.json"),
            "--no-llm",
            "--no-embeddings",
            "-q",
            "-o",
            str(out),
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert out.is_file()
        assert "Autopsy Report" in out.read_text()

    def test_analyze_json_format_is_parseable(self) -> None:
        proc = _run(
            "analyze",
            str(SAMPLE / "loop_failure.json"),
            "--no-llm",
            "--no-embeddings",
            "-q",
            "-f",
            "json",
        )
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["run_id"] == "run_loop_001"
        assert isinstance(payload["health_score"], int)

    def test_analyze_writes_artifacts_dir(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "patches"
        proc = _run(
            "analyze",
            str(SAMPLE / "loop_failure.json"),
            "--no-llm",
            "--no-embeddings",
            "-q",
            "--artifacts",
            str(artifacts),
        )
        assert proc.returncode == 1
        assert (artifacts / "manifest.json").is_file()

    def test_analyze_unknown_format_fails_fast(self) -> None:
        proc = _run("analyze", str(SAMPLE / "successful_run.json"), "-f", "xml", "--no-llm")
        assert proc.returncode == 2
        assert "Unknown format" in proc.stdout

    def test_analyze_missing_file_exits_2(self) -> None:
        proc = _run("analyze", str(REPO_ROOT / "does-not-exist.json"), "--no-llm")
        assert proc.returncode == 2

    def test_analyze_bad_provider_still_runs_deterministic(self) -> None:
        proc = _run(
            "analyze",
            str(SAMPLE / "successful_run.json"),
            "--no-llm",
            "--no-embeddings",
            "-q",
            "--provider",
            "bogus_provider",
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout


class TestValidateCommand:
    def test_validate_valid_trace(self) -> None:
        proc = _run("validate", str(SAMPLE / "successful_run.json"))
        assert proc.returncode == 0
        assert "Trace is valid" in proc.stdout

    def test_validate_malformed_json_exits_2(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"run_id": ', encoding="utf-8")
        proc = _run("validate", str(bad))
        assert proc.returncode == 2
        assert "Invalid" in proc.stdout or "Error" in proc.stdout


class TestSummaryCommand:
    def test_summary_prints_table(self) -> None:
        proc = _run("summary", str(SAMPLE / "loop_failure.json"))
        assert proc.returncode == 0
        assert "run_loop_001" in proc.stdout
        assert "Signals Detected" in proc.stdout


class TestCompareCommand:
    @pytest.mark.parametrize("command", ["diff", "compare"])
    def test_diff_alias(self, command: str) -> None:
        proc = _run(
            command,
            str(EXAMPLES / "loop_failure.json"),
            str(EXAMPLES / "loop_fixed.json"),
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "Trace comparison" in proc.stdout

    def test_compare_json_output(self) -> None:
        proc = _run(
            "compare",
            str(EXAMPLES / "loop_failure.json"),
            str(EXAMPLES / "loop_fixed.json"),
            "-f",
            "json",
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert "run_id_a" in payload
        assert "advanced" in payload


class TestMiscCommands:
    def test_fixes_command(self) -> None:
        proc = _run("fixes", str(SAMPLE / "loop_failure.json"))
        assert proc.returncode == 0
        assert "Fix Suggestion" in proc.stdout

    def test_agent_flow_command(self) -> None:
        proc = _run("agent-flow", str(SAMPLE / "successful_run.json"))
        assert proc.returncode == 0
        assert "Agent Flow" in proc.stdout

    def test_config_command(self) -> None:
        proc = _run("config")
        assert proc.returncode == 0
        assert "Agent Autopsy Configuration" in proc.stdout

    def test_telemetry_status_command(self) -> None:
        proc = _run("telemetry", "status")
        assert proc.returncode == 0
        assert "Telemetry" in proc.stdout

    def test_telemetry_bad_action_exits_2(self) -> None:
        proc = _run("telemetry", "sideways")
        assert proc.returncode == 2

    def test_benchmark_command(self, tmp_path: Path) -> None:
        proc = _run("benchmark", "--traces-dir", str(SAMPLE), "--limit", "10")
        assert proc.returncode == 0
        assert "Benchmark Summary" in proc.stdout

    def test_replay_command(self) -> None:
        proc = _run("replay", str(SAMPLE / "successful_run.json"), "--delay", "0.001")
        assert proc.returncode == 0
        assert "message" in proc.stdout


class TestModuleEntryPoints:
    def test_python_dash_m_agent_autopsy(self) -> None:
        proc = _run("config")
        assert proc.returncode == 0


class TestAnalyzeExitContractOnCleanRunWithAuthLanguage:
    def test_auth_prose_in_clean_trace_exits_zero(self, tmp_path: Path) -> None:
        """Auth-related wording inside prompts/outputs of a healthy run must
        not trip the findings gate (detector-level regression guard)."""
        fixture = {
            "run_id": "run_auth_prose_001",
            "status": "success",
            "start_time": "2024-01-15T11:00:00Z",
            "end_time": "2024-01-15T11:00:30Z",
            "model": "gpt-4",
            "input": {"goal": "Explain how oauth authorization works"},
            "events": [
                {
                    "type": "message",
                    "role": "user",
                    "content": "Please explain how oauth authorization works",
                    "timestamp": "2024-01-15T11:00:01Z",
                },
                {
                    "type": "llm_call",
                    "name": "gpt-4",
                    "input": {"prompt": "explain how oauth authorization works"},
                    "output": {"text": "oauth authorization uses grant flows"},
                    "latency_ms": 120,
                    "token_count": 40,
                    "timestamp": "2024-01-15T11:00:02Z",
                },
                {
                    "type": "tool_call",
                    "name": "docs",
                    "input": {"query": "oauth authorization code flow"},
                    "output": {"excerpt": "authorization code grant"},
                    "latency_ms": 90,
                    "token_count": 20,
                    "timestamp": "2024-01-15T11:00:03Z",
                },
            ],
        }
        path = tmp_path / "auth_prose_clean.json"
        path.write_text(json.dumps(fixture))

        proc = _run("analyze", str(path), "--no-llm", "--no-embeddings", "-q")
        assert proc.returncode == 0, proc.stderr + proc.stdout


class TestParseErrorExitContract:
    """Every trace-loading command exits 2 on parse failure, per the
    documented contract: 0 clean, 1 findings, 2 tool/parse error."""

    def _bad_file(self, tmp_path: Path) -> Path:
        bad = tmp_path / "bad.json"
        bad.write_text('{"run_id": ', encoding="utf-8")
        return bad

    def test_summary_parse_error_exits_2(self, tmp_path: Path) -> None:
        proc = _run("summary", str(self._bad_file(tmp_path)))
        assert proc.returncode == 2, proc.stdout

    def test_fixes_parse_error_exits_2(self, tmp_path: Path) -> None:
        proc = _run("fixes", str(self._bad_file(tmp_path)))
        assert proc.returncode == 2, proc.stdout

    def test_agent_flow_parse_error_exits_2(self, tmp_path: Path) -> None:
        proc = _run("agent-flow", str(self._bad_file(tmp_path)))
        assert proc.returncode == 2, proc.stdout


class TestEmptyTraceContract:
    def test_analyze_empty_trace_is_tool_error_not_clean(self, tmp_path: Path) -> None:
        """A trace with zero events cannot prove a clean run; exiting 0
        would let CI pass on garbage input."""
        empty = tmp_path / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        proc = _run("analyze", str(empty), "--no-llm", "--no-embeddings", "-q")
        assert proc.returncode == 2, proc.stdout
        assert "no events" in proc.stdout.lower()

    def test_summary_empty_trace_exits_2(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        proc = _run("summary", str(empty))
        assert proc.returncode == 2, proc.stdout
