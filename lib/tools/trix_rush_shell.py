"""Streaming persistent-bash transport for Trix ToolRush (Linux).

Port of ToolRush v2 tools/toolrush_shell.py (github.com/OnlyTerp/toolrush,
MIT). One long-lived `bash --noprofile --norc -s` per LocalEnvironment;
frames travel through its stdin with uuid markers; stdout streams to the
consumer through a real pipe with OS backpressure. The snapshot re-dump
inside each frame is rewritten to commit ATOMICALLY and SYNCHRONOUSLY via
os.replace before completion is signaled. If the wrapped command's text
does not match the expected upstream shape, the frame is refused and the
caller falls back to the stock spawn path (fail closed).
"""
import os
import re
import shlex
import signal
import subprocess
import threading
import uuid

# The broker itself must never carry provider/session credentials: only the
# bone-dry basics needed to exec children. Frame-level exports carry the
# per-command run environment inside a subshell.
_BROKER_KEEP = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR', 'TERMINFO')


class WarmShell:
    """One persistent bash broker owned by a LocalEnvironment."""

    def __init__(self, local, owner):
        from tools.environments.local import _find_bash, _make_run_env
        bash = _find_bash()
        sanitized = _make_run_env(owner.env)
        env = {k: v for k, v in sanitized.items() if k in _BROKER_KEEP}
        self.local = local
        self.owner = owner
        self.lock = threading.Lock()
        self.dead = False
        self.proc = subprocess.Popen(
            [bash, '--noprofile', '--norc', '-s'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=0,
            start_new_session=True,
        )

    def close(self):
        self.dead = True
        proc = self.proc
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def build_frame(owner, local, command):
    """Wrap a stock wrapped-command into a broker frame.

    Returns (frame_bytes, begin_marker, end_prefix, commit) where commit is
    (reserved_tmp, snapshot_path, ready_marker) or None when the wrapped
    command carries no re-dump to rewrite.
    """
    from tools.environments.local import _make_run_env
    uid = uuid.uuid4().hex
    begin = f'__TRXB_{uid}__'
    end_prefix = f'__TRXE_{uid}:'
    end = end_prefix + '$__tr_rc'

    body = command
    commit = None
    if owner._snapshot_ready:
        snap = owner._snapshot_path
        reserved = f'{snap}.trix.{uid}.tmp'
        ready = f'{reserved}.ready'
        alloc_needle = '__hermes_snap_tmp=$(mktemp '
        alloc_at = body.find(alloc_needle)
        if alloc_at >= 0:
            close_at = body.find(')', alloc_at + len(alloc_needle))
            mv_needle = '&& mv -f "$__hermes_snap_tmp" '
            mv_at = body.find(mv_needle)
            if close_at > 0 and mv_at > close_at:
                mv_end = body.find('; }', mv_at)
                if mv_end > 0:
                    body = (body[:alloc_at]
                            + f'__hermes_snap_tmp={shlex.quote(reserved)} '
                            + body[close_at + 1:])
                    # recompute positions after the alloc rewrite
                    mv_at = body.find(mv_needle)
                    mv_end = body.find('; }', mv_at)
                    body = body[:mv_at] + f'&& : > {shlex.quote(ready)} ' + body[mv_end:]
                    commit = (reserved, snap, ready)
            if commit is None:
                # Unexpected wrapper shape: refuse acceleration for this frame.
                return None

    env = _make_run_env(owner.env)
    exports = '\n'.join(
        f'export {k}={shlex.quote(v)}' for k, v in env.items()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and isinstance(v, str))
    frame = (
        f'printf "{begin}\\n"\n'
        '(\n'
        f'{exports}\n'
        f'{body}\n'
        ') </dev/null\n'
        f'__tr_rc=$?\n'
        f'printf "{end_prefix}%s\\n" "$__tr_rc"\n'
    )
    return frame.encode('utf-8'), begin, end_prefix, commit


class WarmHandle:
    """A Popen-shaped facade streaming one frame through the broker."""

    def __init__(self, shell, frame, begin, end_prefix, commit):
        self._shell = shell
        self._commit = commit
        self.pid = shell.proc.pid
        self.returncode = None
        self._done = threading.Event()
        self.args = ['warm-shell']
        self._read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(self._read_fd, 'rb', buffering=0)
        self._worker = threading.Thread(target=self._run, name='trix-rush-warm',
                                        args=(frame, begin, end_prefix), daemon=True)
        self._worker.start()

    def _run(self, frame, begin, end_prefix):
        shell = self._shell
        rc = None
        try:
            shell.proc.stdin.write(frame)
            shell.proc.stdin.flush()
            buf = b''
            broker_fd = shell.proc.stdout.fileno()
            while True:
                chunk = os.read(broker_fd, 16384)
                if not chunk:
                    rc = 1  # broker died mid-frame
                    shell.dead = True
                    break
                buf += chunk
                begin_b = (begin + '\n').encode()
                end_b = end_prefix.encode()
                while True:
                    at = buf.find(begin_b)
                    if at < 0:
                        break
                    buf = buf[:at] + buf[at + len(begin_b):]
                at = buf.rfind(end_b)
                if at >= 0:
                    line_end = buf.find(b'\n', at)
                    if line_end >= 0:
                        try:
                            rc = int(buf[at + len(end_b):line_end].strip())
                        except ValueError:
                            rc = 1
                        buf = buf[:at] + buf[line_end + 1:]
                        break
                # Emit everything except bytes that could still be part of a
                # marker (a partial prefix, or a complete end marker waiting
                # for its rc digits and newline). Streaming stays real.
                hold = 0
                for marker in (begin_b, end_b):
                    for k in range(1, min(len(marker), len(buf)) + 1):
                        if buf.endswith(marker[:k]):
                            hold = max(hold, k)
                at = buf.rfind(end_b)
                if at >= 0:
                    hold = max(hold, len(buf) - at)
                if len(buf) > hold:
                    os.write(self._write_fd, buf[:-hold] if hold else buf)
                    buf = buf[-hold:] if hold else b''
            if buf:
                os.write(self._write_fd, buf)
            # Snapshot commit is synchronous and precedes completion.
            if self._commit is not None and not shell.dead:
                reserved, snap, ready = self._commit
                if os.path.exists(ready):
                    try:
                        os.replace(reserved, snap)
                    except OSError:
                        rc = 1
                        shell.dead = True
                        os.write(self._write_fd, b'[Trix ToolRush snapshot commit failed]\n')
                else:
                    # dump failed upstream: rm -f already removed the temp
                    pass
        except Exception:
            rc = 1
            shell.dead = True
        finally:
            for path in ([self._commit[0], self._commit[2]] if self._commit else []):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            try:
                os.close(self._write_fd)
            except OSError:
                pass
            self.returncode = rc if rc is not None else 1
            self._done.set()
            shell.lock.release()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        # The handle owns the frame's whole process tree via the broker group.
        self._shell.close()
