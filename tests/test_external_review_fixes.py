"""Regressions for the external-review findings AUX-01..AUX-05, through the real CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_autopsy import api
from agent_autopsy.ingestion.parser import parse_trace_data

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'traces'
_MISSING = object()


def write_trace(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def cli(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env['AUTOPSY_NO_EMBEDDINGS'] = '1'
    env['NO_COLOR'] = '1'
    env['COLUMNS'] = '200'
    return subprocess.run(
        [sys.executable, '-m', 'agent_autopsy.cli', *args, '--no-llm', '--no-embeddings'],
        capture_output=True, text=True, env=env,
    )


def clean_trace(status=_MISSING) -> dict:
    payload = json.loads((EXAMPLES / 'successful_run.json').read_text())
    if status is _MISSING:
        payload.pop('status')
    else:
        payload['status'] = status
    return payload


# AUX-03: an unrecognised, missing, in-progress or non-string status is not a success.

@pytest.mark.parametrize('status', [
    'running', 'in_progress', 'pending', 'started', 'RUNNING', 'crashed', '',
    False, True, None, 0, 1, ['success'], {'state': 'success'}, _MISSING,
])
def test_unverifiable_status_parses_as_unknown(status):
    trace = parse_trace_data(clean_trace(status))
    assert trace.status.value == 'unknown'


@pytest.mark.parametrize('status,error,expected', [
    ('Completed', None, 'success'),
    ('running', 'worker crashed', 'failed'),
])
def test_recognised_status_and_error_precedence_are_kept(status, error, expected):
    payload = clean_trace(status)
    if error:
        payload['error'] = error
    assert parse_trace_data(payload).status.value == expected


@pytest.mark.parametrize('status', ['running', _MISSING])
def test_unknown_status_exits_1_and_reports_unknown(tmp_path, status):
    trace = write_trace(tmp_path / 'trace.json', clean_trace(status))
    out_json = tmp_path / 'report.json'
    result = cli('analyze', str(trace), '-q', '-f', 'json', '-o', str(out_json))
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(out_json.read_text())['status'] == 'unknown'

    out_md = tmp_path / 'report.md'
    result = cli('analyze', str(trace), '-f', 'markdown', '-o', str(out_md))
    assert result.returncode == 1, result.stdout + result.stderr
    assert '- **Status:** unknown' in out_md.read_text()
    assert 'Status' in result.stdout and 'unknown' in result.stdout


def test_benchmark_does_not_count_unknown_status_as_success(tmp_path):
    from agent_autopsy.advanced import benchmark_trace_directory

    write_trace(tmp_path / 'ok.json', clean_trace('success'))
    write_trace(tmp_path / 'running.json', clean_trace('running'))
    result = benchmark_trace_directory(tmp_path)
    assert result.success_rate == pytest.approx(0.5)


# AUX-01: artifact writes are atomic and never alias the input trace.

def test_artifacts_dir_holding_input_named_manifest_is_refused(tmp_path):
    adir = tmp_path / 'artifacts'
    adir.mkdir()
    source = adir / 'manifest.json'
    source.write_bytes((EXAMPLES / 'hallucinated_tool.json').read_bytes())
    original = source.read_bytes()
    result = cli('analyze', str(source), '-q', '--artifacts', str(adir))
    assert result.returncode == 2, result.stdout + result.stderr
    assert source.read_bytes() == original
    assert 'input trace' in result.stdout
    assert sorted(p.name for p in adir.iterdir()) == ['manifest.json']


@pytest.mark.parametrize('alias', ['symlink', 'hardlink'])
def test_save_all_refuses_artifact_path_that_is_the_input(tmp_path, alias):
    from agent_autopsy.output import ArtifactGenerator

    source = tmp_path / 'trace.json'
    source.write_bytes((EXAMPLES / 'hallucinated_tool.json').read_bytes())
    original = source.read_bytes()
    trace = api.load_trace(source)
    adir = tmp_path / 'artifacts'
    adir.mkdir()
    if alias == 'symlink':
        (adir / 'manifest.json').symlink_to(source)
    else:
        os.link(source, adir / 'manifest.json')
    generator = ArtifactGenerator(trace, api.run_preanalysis(trace))
    with pytest.raises(ValueError, match='input trace'):
        generator.save_all(adir, source_path=source)
    assert source.read_bytes() == original
    assert sorted(p.name for p in adir.iterdir()) == ['manifest.json']


def test_failed_artifact_write_keeps_previous_file(tmp_path, monkeypatch):
    from agent_autopsy.output import ArtifactGenerator
    from agent_autopsy.utils import atomic

    trace = api.load_trace(EXAMPLES / 'hallucinated_tool.json')
    adir = tmp_path / 'artifacts'
    adir.mkdir()
    previous = adir / 'manifest.json'
    previous.write_text('{"previous": true}')

    def fail_replace(src, dst):
        raise OSError('disk full')

    monkeypatch.setattr(atomic.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        ArtifactGenerator(trace, api.run_preanalysis(trace)).save_all(adir)
    assert previous.read_text() == '{"previous": true}'
    assert not [p for p in adir.iterdir() if p.name.endswith('.tmp')]


# AUX-02: optional latency/token metadata absence is informational only.

def test_missing_optional_metadata_is_an_unscored_note(tmp_path):
    trace = write_trace(tmp_path / 'trace.json', {
        'run_id': 'no-timing', 'status': 'success', 'tools': ['fetch'],
        'events': [
            {'type': 'tool_call', 'name': 'fetch', 'input': {'q': 1}, 'output': 'a'},
            {'type': 'tool_call', 'name': 'fetch', 'input': {'q': 2}, 'output': 'b'},
        ],
    })
    out_json = tmp_path / 'report.json'
    result = cli('analyze', str(trace), '-q', '-f', 'json', '-o', str(out_json))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(out_json.read_text())
    assert report['preanalysis']['signals'] == []
    assert report['health_score'] == 100
    notes = report['preanalysis']['notes']
    assert [n['type'] for n in notes] == ['contract_missing_metadata'] * 2
    assert [n['events'] for n in notes] == [[0], [1]]

    out_md = tmp_path / 'report.md'
    assert cli('analyze', str(trace), '-q', '-f', 'markdown', '-o', str(out_md)).returncode == 0
    md = out_md.read_text()
    assert '## Informational Notes' in md
    assert 'Missing optional metadata: latency_ms' in md


# AUX-04: contract duplicates of another finding are labelled as such.

def test_contract_duplicates_are_labelled_without_changing_score(tmp_path):
    out_md = tmp_path / 'report.md'
    result = cli('analyze', str(EXAMPLES / 'hallucinated_tool.json'), '-q', '-f', 'markdown', '-o', str(out_md))
    assert result.returncode == 1
    md = out_md.read_text()
    assert '- **Health Score:** 77/100' in md
    sections = md.split('### Contract Unknown Tool (high)')[1:]
    assert len(sections) == 2
    for section in sections:
        assert 'Same event as the Hallucinated Tool finding' in section.split('---')[0]
    assert md.count('Same event as the') == 2


# AUX-05: the result panel cannot be read as run success.

@pytest.mark.parametrize('command', ['analyze', 'autopsy-run'])
def test_result_panel_names_trace_status_not_success(tmp_path, command):
    args = [command, str(EXAMPLES / 'hallucinated_tool.json'), '-o', str(tmp_path / 'report.md')]
    result = cli(*args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Analysis Status' not in result.stdout
    assert 'SUCCESS' not in result.stdout
    assert 'Analysis completed: yes' in result.stdout
    assert 'Trace status: failed' in result.stdout
