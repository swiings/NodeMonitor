"""
listener_proc.py — Manages NMListener as a child subprocess of NMDisplay.

NMDisplay can start and stop NMListener without a separate terminal.
Stdout + stderr are captured into a rolling ring buffer (last 200 lines)
and served via GET /api/listener/log so the browser can display a live
packet stream.

Python resolution order for running NMListener:
  1. NMListener/.venv/bin/python3  (NMListener's own venv, if it exists)
  2. sys.executable                 (same Python NMDisplay is running under)
"""

import collections
import os
import subprocess
import sys
import threading
from typing import Optional

_here         = os.path.dirname(os.path.abspath(__file__))
_LISTENER_DIR = os.path.abspath(os.path.join(_here, "..", "NMListener"))
_MAIN_PY      = os.path.join(_LISTENER_DIR, "main.py")

# Prefer NMListener's own venv if present
_VENV_PY = os.path.join(_LISTENER_DIR, ".venv", "bin", "python3")
_PYTHON  = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable

LOG_MAXLEN = 200   # lines kept in the ring buffer

_proc:   Optional[subprocess.Popen] = None
_log:    collections.deque           = collections.deque(maxlen=LOG_MAXLEN)
_lock:   threading.Lock              = threading.Lock()
_reader: Optional[threading.Thread] = None


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def start() -> dict:
    """Start NMListener subprocess. No-op if already running."""
    global _proc, _log, _reader
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return status()   # already running

        _log = collections.deque(maxlen=LOG_MAXLEN)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"   # force line-by-line stdout flush
        _proc = subprocess.Popen(
            [_PYTHON, "-u", _MAIN_PY],  # -u = unbuffered
            cwd=_LISTENER_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            env=env,
        )
        _reader = threading.Thread(target=_read_loop, daemon=True)
        _reader.start()
    return status()


def stop() -> dict:
    """Stop NMListener subprocess gracefully, then force-kill if needed."""
    global _proc
    with _lock:
        p = _proc
        if p is None:
            return status()
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        _proc = None
    return status()


def status() -> dict:
    """Return current listener state: stopped | running | error."""
    with _lock:
        if _proc is None:
            return {"state": "stopped", "pid": None}
        rc = _proc.poll()
        if rc is None:
            return {"state": "running", "pid": _proc.pid}
        return {"state": "error", "pid": None, "returncode": rc}


def get_log(n: int = 80) -> list[str]:
    """Return the last n lines from the listener's stdout."""
    with _lock:
        lines = list(_log)
    return lines[-n:]




# ------------------------------------------------------------------
# Internal
# ------------------------------------------------------------------

def _read_loop() -> None:
    """Background thread: drain subprocess stdout into the ring buffer."""
    proc = _proc
    try:
        for line in proc.stdout:
            with _lock:
                _log.append(line.rstrip())
    except Exception:
        pass
