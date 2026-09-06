"""Snapshot lane: exclusion-refresh must fail CLOSED (VAL-SAFE-05 / reviewer B1).

Port of ToolRush v2 test_toolrush_snapshot_failclosed.py for Trix ToolRush.
Runs against the live tree with the snapshot lane installed in-memory.

Regression coverage for the fail-open defect in
``BaseEnvironment._snapshot_excluded_passthrough_names``: a failed refresh
must latch a monotonic marker, dump sites must skip unfiltered dumps while
latched, and the dump helper belt-unsets ``BUZZ_*`` even with an empty set.
"""
import os
import shlex
import subprocess

import pytest

from conftest import ensure_lanes_installed  # noqa: F401

ensure_lanes_installed()  # patches snapshot lane in-memory for this process

from tools.environments.base import BaseEnvironment, _export_dump_excluding_session_vars

SNAP_SEED_VAR = "HERMES_SNAPSEED_TOKEN"
SNAP_SEED_VALUE = "seed1"
PROBE_SECRET = "nsec1fastrabbit-not-a-real-key"


def _boom(*args, **kwargs):
    raise RuntimeError("injected exclusion-refresh failure")


def _bash_exe() -> str:
    from tools.environments.local import _find_bash

    return _find_bash()


class _StubBashEnv(BaseEnvironment):
    """Minimal concrete BaseEnvironment that really runs bash."""

    is_local = False
    _profile_scoped_passthrough = True
    _additional_names: tuple = ()

    def _additional_profile_scoped_passthrough_names(self):
        return tuple(self._additional_names)

    def _quote_shell_path(self, path: str) -> str:
        return shlex.quote(path)

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        popen_kwargs = {"start_new_session": True} if os.name != "nt" else {}
        return subprocess.Popen(
            [_bash_exe(), "-c", cmd_string],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=os.environ.copy(),
            cwd=os.getcwd(),
            **popen_kwargs,
        )

    def cleanup(self):
        return None


def _new_stub(additional_names=()):
    env = _StubBashEnv.__new__(_StubBashEnv)
    env._additional_names = tuple(additional_names)
    env._snapshot_passthrough_names = set()
    return env


def _seed_snapshot(env):
    seeded = f"export {SNAP_SEED_VAR}={SNAP_SEED_VALUE}\n"
    with open(env._snapshot_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(seeded)
    return seeded


def _snap_text(env):
    with open(env._snapshot_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class TestExclusionRefreshFailClosed:
    def test_refresh_error_keeps_previous_set_and_latches_broken(self):
        from agent import secret_scope as ss

        env = _new_stub(("BUZZ_PREVIOUS_PROFILE",))
        env._snapshot_passthrough_names.add("BUZZ_PREVIOUS_PROFILE")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            excluded = env._snapshot_excluded_passthrough_names()

        assert excluded == ("BUZZ_PREVIOUS_PROFILE",)
        assert getattr(env, "_snapshot_exclusion_broken", False) is True

    def test_passthrough_call_failure_also_latches_broken(self):
        import tools.env_passthrough as ep
        from agent import secret_scope as ss

        env = _new_stub(("BUZZ_PREVIOUS_PROFILE",))
        env._snapshot_passthrough_names.add("BUZZ_PREVIOUS_PROFILE")

        ss.set_multiplex_active(True)
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(ep, "get_all_passthrough", _boom)
                excluded = env._snapshot_excluded_passthrough_names()
        finally:
            ss.set_multiplex_active(False)

        assert excluded == ("BUZZ_PREVIOUS_PROFILE",)
        assert getattr(env, "_snapshot_exclusion_broken", False) is True

    def test_first_dump_failure_latches_broken(self):
        from agent import secret_scope as ss

        env = _new_stub(("BUZZ_MANAGED_AGENT",))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            excluded = env._snapshot_excluded_passthrough_names()

        assert excluded == ()
        assert getattr(env, "_snapshot_exclusion_broken", False) is True

    def test_healthy_refresh_does_not_latch_broken(self, monkeypatch):
        from agent import secret_scope as ss

        monkeypatch.setenv("BUZZ_MANAGED_AGENT", "1")
        env = _new_stub(("BUZZ_MANAGED_AGENT",))

        ss.set_multiplex_active(True)
        try:
            excluded = env._snapshot_excluded_passthrough_names()
        finally:
            ss.set_multiplex_active(False)

        assert "BUZZ_MANAGED_AGENT" in excluded
        assert getattr(env, "_snapshot_exclusion_broken", False) is False

    def test_broken_marker_is_monotonic_after_recovery(self):
        from agent import secret_scope as ss

        env = _new_stub(("BUZZ_MANAGED_AGENT",))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            env._snapshot_excluded_passthrough_names()
        assert getattr(env, "_snapshot_exclusion_broken", False) is True

        ss.set_multiplex_active(True)
        try:
            env._snapshot_excluded_passthrough_names()
        finally:
            ss.set_multiplex_active(False)

        assert getattr(env, "_snapshot_exclusion_broken", False) is True


class TestSnapshotDumpFailClosed:
    def test_init_session_skips_dump_when_exclusion_refresh_broken(self, tmp_path):
        from agent import secret_scope as ss

        env = _StubBashEnv.__new__(_StubBashEnv)
        env._additional_names = ("BUZZ_PROBE_TOKEN",)
        env._snapshot_passthrough_names = set()
        env._profile_scoped_passthrough = True
        BaseEnvironment.__init__(env, cwd="~", timeout=30, env={})
        env._snapshot_path = str(tmp_path / "hermes-snap-test.sh")
        seeded = _seed_snapshot(env)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            env.init_session()

        text = _snap_text(env)
        assert seeded in text
        assert "BUZZ_PROBE_TOKEN" not in text

    def test_init_session_dumps_when_refresh_healthy(self, tmp_path):
        env = _StubBashEnv.__new__(_StubBashEnv)
        env._additional_names = ()
        env._snapshot_passthrough_names = set()
        env._profile_scoped_passthrough = True
        BaseEnvironment.__init__(env, cwd="~", timeout=30, env={})
        env._snapshot_path = str(tmp_path / "hermes-snap-test.sh")
        _seed_snapshot(env)

        env.init_session()

        text = _snap_text(env)
        assert SNAP_SEED_VAR not in text

    def test_wrap_command_skips_redump_when_exclusion_refresh_broken(self, tmp_path):
        from agent import secret_scope as ss

        env = _new_stub(("BUZZ_PREVIOUS_PROFILE",))
        env._snapshot_ready = True
        env._session_id = "testsnapfail"
        env._cwd_marker = "__HERMES_CWD_testsnapfail__"
        env._snapshot_path = str(tmp_path / "hermes-snap-test.sh")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            wrapped = env._wrap_command("true", "/tmp")

        assert "export -p" not in wrapped
        assert "mktemp" not in wrapped

    def test_wrap_command_keeps_redump_when_healthy(self, tmp_path):
        env = _new_stub(())
        env._snapshot_ready = True
        env._session_id = "testsnapok"
        env._cwd_marker = "__HERMES_CWD_testsnapok__"
        env._snapshot_path = str(tmp_path / "hermes-snap-test.sh")

        wrapped = env._wrap_command("true", "/tmp")

        assert "export -p" in wrapped
        assert "mktemp" in wrapped


class TestSnapshotBeltUnset:
    def test_belt_unsets_known_secret_prefixes_even_with_empty_set(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("BUZZ_TEST_TOKEN", PROBE_SECRET)
        monkeypatch.setenv("HAPPY_UNRELATED", "fine-to-persist")

        out_path = tmp_path / "belt-snap.sh"
        dump = _export_dump_excluding_session_vars(shlex.quote(str(out_path)), ())
        subprocess.run(
            [_bash_exe(), "-c", dump],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        assert "BUZZ_TEST_TOKEN" not in text
        assert PROBE_SECRET not in text
        assert "HAPPY_UNRELATED" in text


class TestEndToEndFailClosed:
    """Real-bash end-to-end (POSIX lane; upstream ran this on git-bash)."""

    def test_cross_profile_secret_never_persists_fail_closed(self, monkeypatch, tmp_path):
        from agent import secret_scope as ss

        monkeypatch.setenv("BUZZ_TEST_TOKEN", PROBE_SECRET)
        env = _StubBashEnv.__new__(_StubBashEnv)
        env._additional_names = ("BUZZ_TEST_TOKEN",)
        env._snapshot_passthrough_names = set()
        env._profile_scoped_passthrough = True
        BaseEnvironment.__init__(env, cwd="~", timeout=30, env={})
        env._snapshot_path = str(tmp_path / "hermes-snap-test.sh")
        seeded = _seed_snapshot(env)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ss, "is_multiplex_active", _boom)
            env.init_session()
            env._snapshot_ready = True
            wrapped = env._wrap_command("true", str(tmp_path))
            subprocess.run(
                [_bash_exe(), "-c", wrapped],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env=os.environ.copy(),
                cwd=str(tmp_path),
            )

        text = _snap_text(env)
        assert seeded in text
        assert "BUZZ_TEST_TOKEN" not in text
        assert PROBE_SECRET not in text
