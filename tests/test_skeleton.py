"""Skeleton tests: payload schema, compat primitives, runtime gate semantics.

These run WITHOUT a Hermes checkout (config-dependent paths are exercised via
module injection; absent hermes_cli import must fail closed to disabled).
"""
import hashlib
import json
import sys
import types
from pathlib import Path

from conftest import PLUGIN, load_compat, load_runtime

PAYLOADS = sorted(PLUGIN.glob('payload*.json'))


def test_payload_exists():
    assert PAYLOADS, 'at least one payload file must ship'


def test_payload_schema():
    for path in PAYLOADS:
        payload = json.loads(path.read_text())
        assert set(payload) == {'python', 'helpers', 'lanes'}
        assert len(payload['python']) == 2 and all(isinstance(x, int) for x in payload['python'])
        for name, row in payload['helpers'].items():
            assert set(row) == {'file', 'sha256'}
            blob = (PLUGIN / row['file']).read_bytes()
            assert hashlib.sha256(blob).hexdigest() == row['sha256'], f'{name} hash drift'
            assert name == (PLUGIN / row['file']).with_suffix('').name \
                or '.' in name
        for lane, rows in payload['lanes'].items():
            for row in rows:
                assert {'module', 'qualname', 'before', 'after',
                        'before_sha256', 'after_sha256'} <= set(row)
                assert hashlib.sha256(row['after'].encode()).hexdigest() == row['after_sha256']
                if row['before'] is not None:
                    assert hashlib.sha256(row['before'].encode()).hexdigest() == row['before_sha256']


def test_compat_verify_blob():
    compat = load_compat()
    blob = b'payload-bytes'
    digest = hashlib.sha256(blob).hexdigest()
    assert compat.verify_blob(blob, digest) == blob
    try:
        compat.verify_blob(blob, '0' * 64)
    except compat.CompatibilityError:
        pass
    else:
        raise AssertionError('hash mismatch must raise')


def test_compat_source_digest_ignores_comments_and_blank_lines():
    compat = load_compat()
    a = 'def f():\n    """Doc one."""\n    return 1  # note\n'
    b = 'def f():\n\n    """Doc one."""\n\n    return 1\n'
    assert compat.source_digest(a) == compat.source_digest(b)
    assert compat.source_digest(a) != compat.source_digest('def f():\n    return 2\n')


def _runtime_with_config(monkeypatch, config):
    fake = types.ModuleType('hermes_cli.config')
    fake.load_config_readonly = lambda: config
    fake_config_pkg = types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', fake_config_pkg)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', fake)
    return load_runtime()


def test_runtime_env_kill_switch(monkeypatch):
    runtime = _runtime_with_config(
        monkeypatch, {'trix-toolrush': {'enabled': True, 'fast_search': True}})
    assert runtime.enabled('fast_search', ('TRIX_TOOLRUSH_SEARCH', 'TOOLRUSH_SEARCH'))
    for killed in ('TRIX_TOOLRUSH_SEARCH', 'TOOLRUSH_SEARCH'):
        monkeypatch.setenv(killed, '0')
        assert not runtime.enabled('fast_search', ('TRIX_TOOLRUSH_SEARCH', 'TOOLRUSH_SEARCH'))
        monkeypatch.delenv(killed)


def test_runtime_requires_master_and_lane(monkeypatch):
    runtime = _runtime_with_config(
        monkeypatch, {'trix-toolrush': {'enabled': True, 'fast_search': True}})
    assert runtime.enabled('fast_search', ())
    runtime = _runtime_with_config(
        monkeypatch, {'trix-toolrush': {'enabled': True, 'fast_search': False}})
    assert not runtime.enabled('fast_search', ())
    runtime = _runtime_with_config(
        monkeypatch, {'trix-toolrush': {'enabled': False, 'fast_search': True}})
    assert not runtime.enabled('fast_search', ())
    runtime = _runtime_with_config(monkeypatch, {})
    assert not runtime.enabled('fast_search', ())


def test_runtime_toolrush_section_fallback(monkeypatch):
    runtime = _runtime_with_config(
        monkeypatch, {'toolrush': {'enabled': True, 'parallel_reads': True}})
    assert runtime.enabled('parallel_reads', ())


def test_runtime_trix_section_wins_over_toolrush(monkeypatch):
    runtime = _runtime_with_config(monkeypatch, {
        'toolrush': {'enabled': True, 'warm_shell': True},
        'trix-toolrush': {'enabled': True, 'warm_shell': False}})
    assert not runtime.enabled('warm_shell', ())


def test_runtime_fails_closed_without_hermes(monkeypatch):
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', None)  # force ImportError path
    monkeypatch.delenv('TRIX_TOOLRUSH_SEARCH', raising=False)
    runtime = load_runtime()
    assert runtime.enabled('fast_search', ('TRIX_TOOLRUSH_SEARCH',)) is False
