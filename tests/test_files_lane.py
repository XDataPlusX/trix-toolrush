"""Files lane: rg transport bounds + real-entry differential correctness.

Port of ToolRush v2 test_toolrush_rg_bounds.py + test_toolrush_native_search.py.
Differentials drive the REAL search_tool entry point with the lane on/off and
assert identical envelopes (strict JSON); the native lane must never spawn a
shell for search.
"""
import json
import os
import sys
import threading
import time
import uuid
from unittest.mock import patch

import pytest

from conftest import ensure_lanes_installed

ensure_lanes_installed()  # patches files lane in-memory for this process

from tools import file_tools as ft
from tools import trix_rush_rg as trrg
from tools.file_operations import ShellFileOperations


# ---------------------------------------------------------------------------
# Transport bounds (real subprocess, sys.executable as the child)
# ---------------------------------------------------------------------------

def run(script, **kw):
    return trrg.run_rg([sys.executable, '-c', script], cwd=os.getcwd(),
                       env=os.environ.copy(), max_lines=kw.pop('max_lines', 500), **kw)


def test_real_exit_code_not_inferred_from_silence():
    r = run('import sys; sys.exit(2)')
    assert r.exit_code == 2
    assert r.stdout == ''
    assert not r.limited


def test_no_match_exit_one_preserved():
    assert run('import sys; sys.exit(1)').exit_code == 1


def test_output_budget_bounds_giant_unterminated_line():
    r = run("import sys; sys.stdout.write('x'*1000000)", max_bytes=4096)
    assert len(r.stdout) <= 4096
    assert r.limited and r.reason == 'search_output_budget'


def test_line_budget():
    r = run("import sys; sys.stdout.write('x\\n'*10000)", max_lines=3)
    assert r.stdout == 'x\nx\nx\n'


def test_timeout_reaps_process():
    r = run('import time; time.sleep(20)', timeout=0.08)
    assert r.exit_code == 124
    assert r.reason == 'search_timeout'
    assert not any(t.name == 'trix-rush-rg-output' for t in threading.enumerate())


def test_cancel_before_spawn(monkeypatch):
    monkeypatch.setattr(trrg.interrupt, 'is_interrupted', lambda: True)
    monkeypatch.setattr(trrg.subprocess, 'Popen', lambda *a, **kw: pytest.fail('spawned after cancel'))
    assert run('raise SystemExit(0)').exit_code == 130


def test_cancel_inflight_reaps(monkeypatch):
    start = time.monotonic()
    monkeypatch.setattr(trrg.interrupt, 'is_interrupted', lambda: time.monotonic() - start > .1)
    r = run('import time; time.sleep(20)')
    assert r.exit_code == 130
    assert not any(t.name == 'trix-rush-rg-output' for t in threading.enumerate())


def test_metacharacters_never_enter_shell(tmp_path):
    marker = tmp_path / 'must-not-exist'
    r = trrg.run_rg([sys.executable, '-c', 'import sys; print(sys.argv[1])',
                     f'; > {marker}'], cwd=str(tmp_path), env=os.environ.copy(), max_lines=5)
    assert r.exit_code == 0
    assert not marker.exists()
    assert '; >' in r.stdout


# ---------------------------------------------------------------------------
# Differential correctness through the real search_tool entry point
# ---------------------------------------------------------------------------

@pytest.fixture
def lane_config(monkeypatch):
    """Explicit lane config so tests never depend on host configuration."""
    import hermes_cli.config as config

    monkeypatch.setattr(
        config, 'load_config_readonly',
        lambda: {'trix-toolrush': {'enabled': True, 'fast_search': True}})


@pytest.fixture
def local_search(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'
    root.mkdir()
    from tools.environments.local import LocalEnvironment
    env = LocalEnvironment(cwd=str(root))
    ops = ShellFileOperations(env)
    monkeypatch.setattr(ft, '_get_file_ops', lambda task_id='default': ops)
    yield root, ops
    env.cleanup()


def search(root, monkeypatch, lane='1', **kwargs):
    monkeypatch.setenv('TRIX_TOOLRUSH_SEARCH', lane)
    raw = ft.search_tool(path=str(root), task_id='rg-' + uuid.uuid4().hex, **kwargs)
    return json.JSONDecoder().raw_decode(raw)[0]


def test_ignore_file_semantics_preserved(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / '.ignore').write_text('ignored.txt\n')
    (root / 'ignored.txt').write_text('needle SHOULD_NOT_BE_RETURNED\n')
    (root / 'visible.txt').write_text('needle visible\n')
    baseline = search(root, monkeypatch, '0', pattern='needle')
    fast = search(root, monkeypatch, '1', pattern='needle')
    assert fast == baseline
    assert 'SHOULD_NOT_BE_RETURNED' not in json.dumps(fast)


def test_real_search_runs_rg_without_shell(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / 'visible.txt').write_text('needle visible\n')
    with patch.object(ops, '_exec', side_effect=AssertionError('shell search invoked')):
        result = search(root, monkeypatch, '1', pattern='needle')
    assert 'error' not in result, result
    assert result['total_count'] == 1


@pytest.mark.parametrize('kwargs', [
    {'pattern': 'needle'},
    {'pattern': 'needle', 'context': 1},
    {'pattern': 'needle', 'output_mode': 'files_only'},
    {'pattern': 'needle', 'output_mode': 'count'},
    {'pattern': 'needle', 'limit': 2, 'offset': 1},
    {'pattern': 'needle', 'file_glob': '*.txt'},
    {'pattern': '(?<=needle) visible'},
    {'pattern': '(needle)\\1'},
    {'pattern': 'NO_MATCH_SENTINEL'},
    {'pattern': 'needle\\nnext'},
    {'pattern': '*.txt', 'target': 'files'},
    {'pattern': '--needle'},
    {'pattern': '[]'},
])
def test_engine_envelope_differential(local_search, monkeypatch, lane_config, kwargs):
    root, ops = local_search
    (root / 'visible.txt').write_bytes(b'needle visible\r\nnext\r\nneedle\r\n--needle\r\nneedle\r\n')
    (root / 'ignored.txt').write_text('not a matching record\n')
    (root / '.ignore').write_text('ignored.txt\n')
    slow = search(root, monkeypatch, '0', **kwargs)
    fast = search(root, monkeypatch, '1', **kwargs)
    if kwargs.get('target') == 'files':
        slow['files'] = sorted(slow.get('files', []))
        fast['files'] = sorted(fast.get('files', []))
    assert fast == slow


def test_files_no_shell(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / 'visible.txt').write_text('visible\n')
    with patch.object(ops, '_exec', side_effect=AssertionError('shell search invoked')):
        result = search(root, monkeypatch, '1', target='files', pattern='*.txt')
    assert 'error' not in result, result
    assert result['total_count'] == 1


def test_truncated_search_is_strict_json(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / 'many.txt').write_text('needle\n' * 12)
    raw = ft.search_tool('needle', path=root.as_posix(), limit=1, context=1,
                         task_id=uuid.uuid4().hex)
    result = json.loads(raw)
    assert result['truncated'] is True
    assert 'offset=1' in result['_hint']


@pytest.mark.parametrize('mode', ['content', 'files_only', 'count'])
def test_page_bound_reports_more(local_search, monkeypatch, lane_config, mode):
    root, ops = local_search
    for n in range(4):
        (root / f'{n}.txt').write_text('needle\n')
    result = search(root, monkeypatch, '1', pattern='needle', limit=2, output_mode=mode)
    assert result['truncated'] is True
    assert result['total_count_is_lower_bound'] is True
    page = result.get('matches', result.get('files', result.get('counts')))
    assert len(page) == 2


def test_shell_cached_executable_still_allows_native_search(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / 'x.txt').write_text('needle\n')
    # Stock discovery populates the shell resolution cache; the native lane
    # must reuse it instead of silently falling back after one stock call.
    assert search(root, monkeypatch, '0', pattern='needle')['total_count'] == 1
    with patch.object(ops, '_exec', side_effect=AssertionError('fell back after cache')):
        result = search(root, monkeypatch, '1', pattern='needle')
    assert 'error' not in result, result


def test_guard_memoized_per_file(local_search, monkeypatch, lane_config):
    root, ops = local_search
    (root / 'many.txt').write_text('needle\n' * 30)
    calls = []
    real = ft._search_result_read_block_error

    def counting(path, task_id='default'):
        calls.append(path)
        return real(path, task_id)

    monkeypatch.setattr(ft, '_search_result_read_block_error', counting)
    result = search(root, monkeypatch, '1', pattern='needle', limit=30)
    assert result['total_count'] == 30
    assert calls.count(str(root / 'many.txt')) <= 1, calls
