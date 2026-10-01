"""Recorded-trace regressions through the real ingestion and CLI boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_autopsy import api


def write_trace(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def analyze(path: Path, output: Path, *, preexec_fn=None, command='analyze') -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env['AUTOPSY_NO_EMBEDDINGS'] = '1'
    args = [sys.executable, '-m', 'agent_autopsy.cli', command, str(path),
            '--no-llm', '--no-embeddings', '-o', str(output)]
    if command == 'analyze':
        args.extend(['-q', '-f', 'json'])
    return subprocess.run(
        args,
        capture_output=True, text=True, env=env, preexec_fn=preexec_fn,
    )


@pytest.mark.parametrize('format_name', ['generic', 'langgraph', 'langchain', 'paired'])
@pytest.mark.parametrize('empty', ['', {}, []])
def test_empty_primary_response_is_not_replaced_by_stale_alias(tmp_path, format_name, empty):
    event = {'type': 'llm_call', 'output': empty, 'response': 'stale cached answer'}
    payload = {'run_id': 'empty-response', 'status': 'success', 'events': [event]}
    if format_name == 'langgraph':
        payload['thread_id'] = 'thread-1'
    elif format_name == 'langchain':
        payload = {'id': 'empty-response', 'run_type': 'llm',
                   'outputs': empty, 'output': 'stale cached answer'}
    elif format_name == 'paired':
        payload['events'] = [
            {'type': 'llm_start', 'name': 'chat', 'run_id': 'call-1',
             'input': 'question', 'output': 'stale cached answer'},
            {'type': 'llm_end', 'name': 'chat', 'run_id': 'call-1', 'output': empty},
        ]
    trace = write_trace(tmp_path / 'trace.json', payload)
    output = tmp_path / 'report.json'
    result = analyze(trace, output)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(output.read_text())
    findings = [s for s in report['preanalysis']['signals'] if s['type'] == 'empty_response']
    assert len(findings) == 1 and findings[0]['events'] == [0]
    assert 'stale cached answer' not in report['detailed_analysis']


@pytest.mark.parametrize('command', ['analyze', 'autopsy-run'])
@pytest.mark.parametrize('alias', ['direct', 'symlink', 'hardlink', 'suffix'])
def test_report_output_cannot_destroy_the_source_trace(tmp_path, alias, command):
    source = write_trace(tmp_path / ('source.json' if command == 'analyze' else 'source.md'), {
        'status': 'success', 'events': [{'type': 'message', 'content': 'source evidence'}],
    })
    original = source.read_bytes()
    (tmp_path / "original-input.json").write_bytes(original)
    output = source
    if alias == 'suffix':
        output = source.with_suffix('')
    elif alias == 'symlink':
        output = tmp_path / 'report.json'
        output.symlink_to(source)
    elif alias == 'hardlink':
        output = tmp_path / 'report.json'
        os.link(source, output)
    result = analyze(source, output, command=command)
    assert result.returncode == 2, result.stdout + result.stderr
    assert source.read_bytes() == original
    assert 'trace' in result.stdout.lower()


@pytest.mark.skipif(sys.platform == 'win32', reason='requires POSIX file-size limit')
def test_interrupted_report_write_preserves_previous_complete_report(tmp_path):
    import resource

    trace = write_trace(tmp_path / 'trace.json', {
        'status': 'failed', 'events': [{'type': 'llm_call', 'output': ''}],
    })
    output = tmp_path / 'report.json'
    previous = b'{"complete_previous_report": true}\n'
    output.write_bytes(previous)

    def limit_write():
        resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))

    result = analyze(trace, output, preexec_fn=limit_write)
    assert result.returncode != 0
    assert output.read_bytes() == previous
    assert json.loads(trace.read_text())['status'] == 'failed'


def test_numeric_source_ids_keep_parent_relationships_after_normalization(tmp_path):
    trace = write_trace(tmp_path / 'trace.json', {
        'status': 'success',
        'events': [
            {'event_id': 10, 'type': 'message', 'content': 'request'},
            {'event_id': 20, 'parent_event_id': 10, 'type': 'tool_call',
             'name': 'lookup', 'input': {'q': 'request'}, 'output': 'answer'},
        ],
    })
    loaded = api.load_trace(trace)
    (tmp_path / 'normalized.json').write_text(loaded.model_dump_json(indent=2))
    assert loaded.events[1].parent_event_id == loaded.events[0].event_id
    assert loaded.get_event(loaded.events[1].parent_event_id).input == 'request'


def test_offset_timestamps_do_not_turn_spaced_errors_into_retry_storm(tmp_path):
    trace = write_trace(tmp_path / 'trace.json', {
        'status': 'failed', 'start_time': '2026-01-01T10:00:00+02:00',
        'events': [
            {'type': 'tool_call', 'name': 'fetch', 'input': {'attempt': i},
             'error': 'temporary failure', 'timestamp': f'2026-01-01T10:0{i * 2}:00+02:00'}
            for i in range(4)
        ],
    })
    output = tmp_path / 'report.json'
    result = analyze(trace, output)
    assert result.returncode == 1
    report = json.loads(output.read_text())
    assert 'retry_storm' not in {s['type'] for s in report['preanalysis']['signals']}
    loaded = api.load_trace(trace)
    (tmp_path / 'normalized.json').write_text(loaded.model_dump_json(indent=2))
    assert (loaded.events[-1].timestamp - loaded.events[0].timestamp).total_seconds() == 360


def test_invalid_cli_options_do_not_leak_settings_to_later_calls():
    from typer.testing import CliRunner

    from agent_autopsy.cli import app
    from agent_autopsy.utils.config import get_config

    config = get_config()
    config.skip_embeddings = False
    previous_provider = config.llm_provider
    trace = Path(__file__).parent / 'sample_traces/successful_run.json'
    result = CliRunner().invoke(app, ['analyze', str(trace), '--format', 'invalid',
                                    '--no-embeddings', '--provider', 'openai'])
    assert result.exit_code == 2
    assert config.skip_embeddings is False
    assert config.llm_provider == previous_provider


@pytest.mark.parametrize('format_name', ['generic', 'langgraph', 'langchain'])
def test_zero_measurements_are_not_replaced_by_stale_nonzero_aliases(tmp_path, format_name):
    event = {'type': 'llm_call', 'output': 'answer', 'input': {}, 'args': {'stale': True},
             'token_count': 0, 'tokens': 999, 'latency_ms': 0, 'duration_ms': 999}
    payload = {'status': 'success', 'context_window_tokens': 10, 'events': [event]}
    if format_name == 'langgraph':
        payload['thread_id'] = 'thread-1'
    elif format_name == 'langchain':
        payload['callbacks'] = [{'event': 'on_llm_end', **event}]
        del payload['events']
    trace = write_trace(tmp_path / 'trace.json', payload)
    output = tmp_path / 'report.json'
    result = analyze(trace, output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report['trace_summary']['total_tokens'] == 0
    assert report['trace_summary']['duration_ms'] == 0
    loaded = api.load_trace(trace)
    (tmp_path / 'normalized.json').write_text(loaded.model_dump_json(indent=2))
    assert loaded.events[0].input == {}


def test_benchmark_excludes_eventless_inputs_rejected_by_analysis(tmp_path):
    from agent_autopsy.advanced import benchmark_traces

    empty = write_trace(tmp_path / 'empty.json', {'status': 'success', 'events': []})
    failed = write_trace(tmp_path / 'failed.json', {
        'status': 'failed', 'events': [{'type': 'error', 'error': 'failed task'}],
    })
    result = benchmark_traces([empty, failed]).to_dict()
    (tmp_path / 'benchmark-result.txt').write_text(json.dumps(result, indent=2))
    assert result['total_runs'] == 1
    assert result['success_rate'] == 0


def test_monitor_retries_incomplete_trace_with_unchanged_file_timestamp(tmp_path):
    from dataclasses import asdict

    from agent_autopsy.advanced import LiveTraceMonitor

    trace = tmp_path / 'trace.json'
    trace.write_text('{"events": [')
    before = trace.stat()
    monitor = LiveTraceMonitor(tmp_path)
    assert monitor.run_once() == []
    write_trace(trace, {'status': 'failed', 'events': [
        {'type': 'tool_call', 'name': 'fetch', 'input': {'q': 'same'}, 'output': 'no progress'}
        for _ in range(3)
    ]})
    os.utime(trace, ns=(before.st_atime_ns, before.st_mtime_ns))
    alerts = monitor.run_once()
    repeated = monitor.run_once()
    (tmp_path / 'monitor-result.txt').write_text(json.dumps([asdict(a) for a in alerts], indent=2))
    assert any(a.pattern_type == 'infinite_loop' for a in alerts)
    assert repeated == []


def test_offline_report_evidence_is_stable_across_process_hash_seeds(tmp_path):
    trace = write_trace(tmp_path / 'trace.json', {
        'run_id': 'stable-evidence', 'status': 'failed', 'tools': ['search', 'calc', 'email'],
        'events': [{'type': 'tool_call', 'name': 'invented', 'input': {}, 'error': 'unknown tool'}],
    })
    reports = []
    for seed in ['0', '1', '2', '3']:
        output = tmp_path / f'report-{seed}.json'
        env = os.environ.copy()
        env['PYTHONHASHSEED'] = seed
        result = subprocess.run(
            [sys.executable, '-m', 'agent_autopsy.cli', 'analyze', str(trace),
             '--no-llm', '--no-embeddings', '-q', '-f', 'json', '-o', str(output)],
            env=env, capture_output=True, text=True,
        )
        assert result.returncode == 1
        report = json.loads(output.read_text())
        report.pop('generated_at')
        reports.append(report)
    assert all(report == reports[0] for report in reports[1:])


@pytest.mark.parametrize('command', ['analyze', 'autopsy-run'])
def test_successful_recovered_error_without_signals_exits_cleanly(tmp_path, command):
    trace = write_trace(tmp_path / 'trace.json', {
        'run_id': 'recovered', 'status': 'success', 'tools': ['fetch'],
        'events': [
            {'type': 'tool_call', 'name': 'fetch', 'input': {'attempt': 1},
             'output': 'retrying', 'error': 'transient', 'latency_ms': 1},
            {'type': 'tool_call', 'name': 'fetch', 'input': {'attempt': 2},
             'output': 'completed', 'latency_ms': 1},
        ],
    })
    output = tmp_path / 'report.json'
    result = analyze(trace, output, command=command)
    report = json.loads(output.read_text())
    assert report['status'] == 'success'
    assert report['preanalysis']['signals'] == []
    assert report['trace_summary']['errors'] == 1
    assert result.returncode == 0, result.stdout + result.stderr
