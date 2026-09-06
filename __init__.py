"""Trix ToolRush — Linux/Docker acceleration lanes for Hermes Agent.

Linux port of ToolRush v2 (github.com/OnlyTerp/toolrush), shipped as a
standalone Hermes plugin. Core Hermes owns tool semantics and safety gates;
this plugin only replaces transports (native rg argv, batched parallel read
RPC, streaming warm shell) through in-memory, function-level, hash-verified
compatibility patches. Upstream drift degrades a lane loudly instead of
overwriting new code. Nothing on disk inside the Hermes checkout is ever
modified.

Kill-switches (all fail-closed): remove from plugins.enabled, config section
`trix-toolrush: {enabled: false}` (or `toolrush:`), env TRIX_TOOLRUSH_*=0 /
legacy TOOLRUSH_*=0, HERMES_SAFE_MODE=1. Reload requires a fresh host
process; never hot-patch live turns.
"""
import importlib.util
import logging
from pathlib import Path
import re
import shlex
import threading

_SPAWN_LOCK = threading.Lock()
_WARM_ATTR = '_trix_rush_warm'


def _bridge_prefix(owner, local):
    # Kept as a diagnostic hook. Filtering failures propagate; callers may
    # use the ordinary backend, which applies the same filter itself.
    env = local._make_run_env(owner.env)
    return '\n'.join(f'export {k}={shlex.quote(v)}' for k, v in env.items()
                     if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and isinstance(v, str)) + '\n'


def _build_frame(owner, local, command, timeout=None):
    from tools.trix_rush_shell import build_frame
    return build_frame(owner, local, command)


def _apply_terminal_lane():
    from tools.environments import local
    from tools.trix_rush_shell import WarmShell, WarmHandle
    from tools.trix_rush_runtime import enabled
    cls = local.LocalEnvironment
    original = cls._run_bash
    cleanup = cls.cleanup
    kill_process = cls._kill_process
    if getattr(original, '_trix_rush_v1', False):
        return

    def run(owner, command, *, login=False, timeout=120, stdin_data=None):
        if (login or stdin_data is not None
                or not enabled('warm_shell', ('TRIX_TOOLRUSH_PERSIST', 'TOOLRUSH_PERSIST'))):
            return original(owner, command, login=login, timeout=timeout, stdin_data=stdin_data)
        # Background children can retain the broker stdout after a frame.
        # Keep such commands on the existing disposable-process path.
        for line in command.splitlines():
            if line.startswith('eval '):
                try:
                    user_code = shlex.split(line)[1]
                except (ValueError, IndexError):
                    user_code = '&'
                if re.search(r'(?<!&)&(?!&)', user_code):
                    return original(owner, command, login=login, timeout=timeout,
                                    stdin_data=stdin_data)
        shell = None
        acquired = False
        try:
            with _SPAWN_LOCK:
                shell = getattr(owner, _WARM_ATTR, None)
                if shell is None or shell.dead or shell.proc.poll() is not None:
                    if shell is not None:
                        shell.close()
                    shell = WarmShell(local, owner)
                    setattr(owner, _WARM_ATTR, shell)
            acquired = shell.lock.acquire(blocking=False)
            if not acquired:
                # Never queue independent work behind one busy warm shell.
                return original(owner, command, login=login, timeout=timeout,
                                stdin_data=stdin_data)
            frame = _build_frame(owner, local, command)
            if frame is None:
                shell.lock.release()
                acquired = False
                return original(owner, command, login=login, timeout=timeout,
                                stdin_data=stdin_data)
            frame, begin, end_prefix, commit = frame
            return WarmHandle(shell, frame, begin, end_prefix, commit)
        except Exception:
            if acquired:
                shell.lock.release()
            # Frame submission happens only in WarmHandle's worker. No failed
            # submitted command is retried, avoiding duplicated side effects.
            return original(owner, command, login=login, timeout=timeout,
                            stdin_data=stdin_data)

    def clean(owner):
        shell = getattr(owner, _WARM_ATTR, None)
        if shell is not None:
            shell.close()
            setattr(owner, _WARM_ATTR, None)
        return cleanup(owner)

    def kill(owner, proc):
        if isinstance(proc, WarmHandle):
            # The handle owns the full frame tree through the broker's
            # process group.
            proc.kill()
            return
        return kill_process(owner, proc)

    run._trix_rush_v1 = True
    run._trix_rush_original = original
    clean._trix_rush_v1 = True
    run.__name__ = '_run_bash'
    cls._run_bash = run
    cls._kill_process = kill
    cls.cleanup = clean


_COMPAT_STATUS = None


def register(ctx=None):
    global _COMPAT_STATUS
    if _COMPAT_STATUS is None:
        try:
            spec = importlib.util.spec_from_file_location(
                '_trix_toolrush_compat', Path(__file__).with_name('compat.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _COMPAT_STATUS = module.install()
        except Exception as exc:
            _COMPAT_STATUS = {'bootstrap': {'status': 'degraded', 'reason': str(exc)}}
            logging.getLogger(__name__).warning(
                'Trix ToolRush compatibility bootstrap disabled: %s', exc)
            return
    # The warm shell trusts snapshot re-dumps; it only attaches when the
    # snapshot lane's fail-closed fixes are in place.
    if _COMPAT_STATUS.get('snapshot', {}).get('status') == 'ready':
        try:
            _apply_terminal_lane()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                'Trix ToolRush warm shell lane disabled: %s', exc)
    return _COMPAT_STATUS
