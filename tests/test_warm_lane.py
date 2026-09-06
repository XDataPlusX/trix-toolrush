"""Warm shell lane: the installed plugin's persistent-bash transport.

Port of ToolRush v2 test_toolrush_plugin_v2.py (warm suite) for Linux.
Loads the actual plugin by path, registers it, and exercises the patched
LocalEnvironment: exit codes, env export/readback, streaming before
completion, no hidden output cap, synchronous atomic snapshot commit,
revoked-env hygiene, background bypass, timeout reap + recovery.
"""
import importlib.util
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import PLUGIN, ensure_lanes_installed, hermes_root  # noqa: F401

ensure_lanes_installed()

import tools.environments.local as local  # noqa: E402
from tools.environments.local import LocalEnvironment  # noqa: E402
from tools.trix_rush_shell import WarmHandle  # noqa: E402
from tools import trix_rush_runtime as runtime  # noqa: E402


@pytest.fixture
def plugin(monkeypatch):
    for cls, name in [(local.LocalEnvironment, '_run_bash'),
                      (local.LocalEnvironment, 'cleanup'),
                      (local.LocalEnvironment, '_kill_process')]:
        monkeypatch.setattr(cls, name, getattr(cls, name))
    spec = importlib.util.spec_from_file_location('trix_rush_test_plugin',
                                                  PLUGIN / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register(None)
    return module


@pytest.fixture
def warm_config(monkeypatch):
    import hermes_cli.config as config
    monkeypatch.setattr(
        config, 'load_config_readonly',
        lambda: {'trix-toolrush': {'enabled': True, 'warm_shell': True}})


def test_core_read_gates_not_replaced(plugin):
    env = LocalEnvironment(cwd=os.getcwd())
    try:
        # Native read is upstream-owned on Linux and stays enabled locally.
        assert env.__class__._run_bash.__name__ == '_run_bash'
        assert getattr(local.LocalEnvironment._run_bash, '_trix_rush_v1', False)
    finally:
        env.cleanup()


def test_no_asynchronous_snapshot_state(plugin, tmp_path):
    env = local.LocalEnvironment(cwd=str(tmp_path))
    try:
        body = env._wrap_command('export TRIX_PROOF=done', env.cwd)
        built = plugin._build_frame(env, local, body, 5)
        assert built is not None
        frame = built[0].decode()
        assert ' ; } &' not in frame
        assert 'mktemp' not in frame, 'temporary reservation must not fork mktemp'
        assert '__hermes_snap_tmp=' in frame
    finally:
        env.cleanup()


def test_loaded_plugin_exports_exitcodes_and_output(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        for n in range(5):
            r = env.execute(f'export TRIX_PROOF=value{n}')
            assert r['returncode'] == 0, r
            r = env.execute('printf "%s" "$TRIX_PROOF"')
            assert r['returncode'] == 0 and r['output'] == f'value{n}', r
        r = env.execute('printf fail; false')
        assert r['returncode'] == 1 and r['output'] == 'fail', r
    finally:
        env.cleanup()


def test_warm_streams_before_completion(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    handle = None
    try:
        handle = env._run_bash("printf 'early-output'; /usr/bin/sleep 1; printf 'finished'")
        assert isinstance(handle, WarmHandle)
        result = []
        arrived = threading.Event()

        def read():
            result.append(os.read(handle.stdout.fileno(), 3))
            arrived.set()
            while os.read(handle.stdout.fileno(), 4096):
                pass

        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        assert arrived.wait(.7), 'output held until command finished'
        assert result[0] == b'ear'
        assert handle.wait(4) == 0
        worker.join(2)
    finally:
        if handle and handle.poll() is None:
            handle.kill()
        env.cleanup()


def test_warm_output_has_no_hidden_cap(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        command = 'head -c 5000000 /dev/zero | tr "\\0" "x"'
        result = env.execute(command, timeout=20)
        assert result['returncode'] == 0, result
        assert len(result['output']) == 5000000
    finally:
        env.cleanup()


def test_direct_warm_handle_preserves_coreutils_path(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        handle = env._run_bash('command -v sleep; command -v mktemp')
        output = handle.stdout.read().decode()
        assert handle.wait(3) == 0, output
        assert '/sleep' in output and '/mktemp' in output, output
    finally:
        env.cleanup()


def test_warm_snapshot_commit_is_native_and_synchronous(plugin, monkeypatch, tmp_path, warm_config):
    import tools.trix_rush_shell as transport
    env = LocalEnvironment(cwd=str(tmp_path))
    calls = []
    real = transport.os.replace

    def track(src, dst):
        calls.append((src, dst))
        return real(src, dst)

    monkeypatch.setattr(transport.os, 'replace', track)
    try:
        assert env.execute('export TRIX_NATIVE_COMMIT=latest')['returncode'] == 0
        assert calls, 'snapshot commit must be native os.replace, not mv'
        assert env.execute('printf %s "$TRIX_NATIVE_COMMIT"')['output'] == 'latest'
    finally:
        env.cleanup()


def test_warm_does_not_retain_revoked_parent_environment(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        monkeypatch.setenv('TR_REVOCABLE_PROOF', 'first')
        handle = env._run_bash('printf %s "$TR_REVOCABLE_PROOF"')
        assert handle.stdout.read() == b'first' and handle.wait(2) == 0
        monkeypatch.delenv('TR_REVOCABLE_PROOF')
        handle = env._run_bash('printf %s "${TR_REVOCABLE_PROOF-absent}"')
        assert handle.stdout.read() == b'absent' and handle.wait(2) == 0
    finally:
        env.cleanup()


def test_compound_background_commands_do_not_use_shared_broker(plugin, monkeypatch, tmp_path, warm_config):
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        command = "printf first\n( /usr/bin/sleep .1; printf child ) &\nprintf end"
        handle = env._run_bash(env._wrap_command(command, env.cwd))
        assert not isinstance(handle, WarmHandle)
        handle.stdout.read()
        handle.wait(5)
    finally:
        env.cleanup()


def test_warm_timeout_reaps_and_recovers(plugin, monkeypatch, tmp_path, warm_config):
    import time
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        start = time.monotonic()
        result = env.execute('sleep 20', timeout=.2)
        assert result['returncode'] != 0
        assert time.monotonic() - start < 6
        result = env.execute('printf recovered', timeout=5)
        assert result['output'] == 'recovered' and result['returncode'] == 0, result
    finally:
        env.cleanup()


def test_bridge_filter_errors_never_fall_back_to_raw_environment(plugin):
    def broken(*args):
        raise RuntimeError('filter failed')

    fake_local = SimpleNamespace(_make_run_env=broken)
    with pytest.raises(RuntimeError, match='filter failed'):
        plugin._bridge_prefix(SimpleNamespace(env={}), fake_local)
