"""Native ripgrep transport for Trix ToolRush. No search/regex reimplementation.

Linux port of ToolRush v2 tools/toolrush_rg.py (github.com/OnlyTerp/toolrush,
MIT): bounded prefix capture replaces `bash ... | head`; only argv from
internal search builders enters here; no result cache; fresh filesystem
reads each call. The upstream Windows machinery (MSYS path translation,
`.exe` completion, `;`-PATH splitting, case-insensitive env keys, cargo/
winget fallbacks, hide-window flags) collapses to plain POSIX lookups here.
"""
from dataclasses import dataclass
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time

from tools import interrupt

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 16384


@dataclass
class Capture:
    stdout: str
    exit_code: int
    limited: bool = False
    reason: str | None = None


def native_context(ops):
    """Return local cwd/env/rg, or None when shell state cannot be preserved."""
    from tools.environments.local import LocalEnvironment, _make_run_env
    if not isinstance(ops.env, LocalEnvironment):
        return None
    cwd = ops._local_native_path(getattr(ops.env, 'cwd', None) or ops.cwd)
    if cwd is None or not os.path.isdir(cwd):
        return None
    run_env = _make_run_env(ops.env.env)
    # rg observes these shell exports. Read the atomic snapshot, never source
    # or evaluate shell text in Python. Unrepresentable quoting falls back.
    state = getattr(ops.env, '_snapshot_path', None)
    if state and getattr(ops.env, '_snapshot_ready', False):
        try:
            with open(state, encoding='utf-8') as fh:
                snapshot = fh.read(1024 * 1024 + 1)
            if len(snapshot) > 1024 * 1024:
                return None
        except OSError:
            return None
        if re.search(r'(?m)^(?:function\s+)?rg\s*\(\)|^alias rg=', snapshot):
            return None
        relevant = {'RIPGREP_CONFIG_PATH', 'HOME', 'XDG_CONFIG_HOME', 'PATH', 'LANG', 'LC_ALL'}
        for line in snapshot.splitlines():
            if not line.startswith('declare -x '):
                continue
            name = line[11:].split('=', 1)[0]
            if name not in relevant or '=' not in line:
                continue
            raw = line[11:]
            if "$'" in raw or '`' in raw or '$(' in raw:
                return None
            try:
                words = shlex.split(raw, posix=True)
            except ValueError:
                return None
            if len(words) != 1 or '=' not in words[0]:
                return None
            key, value = words[0].split('=', 1)
            run_env.pop(key, None)
            run_env[key] = value
    cached = ops._rg_resolution_cache.get('rg')
    executable = cached if cached and os.path.isabs(cached) else None
    if not executable:
        executable = shutil.which('rg', path=run_env.get('PATH'))
    if not executable:
        candidate = os.path.join(os.path.expanduser('~'), '.cargo', 'bin', 'rg')
        if os.path.isfile(candidate):
            executable = candidate
    if not executable or not os.path.isfile(executable):
        return None
    return cwd, run_env, executable


def run_rg(argv, *, cwd, env, max_lines, timeout=60, max_bytes=MAX_CAPTURE_BYTES):
    """Read bounded output and always reap the process, including cancellation."""
    if interrupt.is_interrupted():
        return Capture('', 130, True, 'search_interrupted')
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks = queue.Queue(maxsize=4)
    stop = threading.Event()

    def pump():
        try:
            while not stop.is_set():
                data = proc.stdout.read1(CHUNK_BYTES)
                if not data:
                    break
                while not stop.is_set():
                    try:
                        chunks.put(data, timeout=0.05)
                        break
                    except queue.Full:
                        pass
        finally:
            while not stop.is_set():
                try:
                    chunks.put(None, timeout=0.05)
                    break
                except queue.Full:
                    pass

    reader = threading.Thread(target=pump, name='trix-rush-rg-output', daemon=True)
    reader.start()
    kept = bytearray()
    line_count = 0
    reason = None
    row_bound = False
    reached_eof = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if interrupt.is_interrupted():
                reason = 'search_interrupted'
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = 'search_timeout'
                break
            try:
                data = chunks.get(timeout=min(remaining, 0.025))
            except queue.Empty:
                continue
            if data is None:
                reached_eof = True
                break
            needed = max_lines - line_count
            pos = -1
            for _ in range(needed):
                pos = data.find(b'\n', pos + 1)
                if pos < 0:
                    break
            if pos >= 0:
                data = data[:pos+1]
                row_bound = True
            room = max_bytes - len(kept)
            if len(data) > room:
                kept.extend(data[:room])
                reason = 'search_output_budget'
                break
            kept.extend(data)
            line_count += data.count(b'\n')
            if row_bound:
                break
    finally:
        stop.set()
        if reached_eof:
            try:
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                reason = 'search_timeout'
        if proc.poll() is None:
            proc.kill()
        code = proc.wait(timeout=5)
        reader.join(timeout=2)
        proc.stdout.close()
    if reason == 'search_timeout':
        code = 124
    elif reason == 'search_interrupted':
        code = 130
    elif row_bound:
        code = 0  # deliberately bounded prefix, as with head (not full scan)
    text = bytes(kept).decode('utf-8', 'replace').replace('\r\n', '\n')
    return Capture(text, code, bool(reason), reason)
