"""
listener_proc.py — Manages NMListener as a child subprocess of NMDisplay.

NMDisplay can start and stop NMListener without a separate terminal.
Stdout + stderr are captured into a rolling ring buffer (last 200 lines)
and served via GET /api/listener/log so the browser can display a live
packet stream.

Command resolution order (dev mode):
  1. NMListener/.venv/bin/python3  (NMListener's own venv, if it exists)
  2. sys.executable                 (same Python NMDisplay is running under)

Frozen mode (PyInstaller .app):
  Re-invokes this executable with --listener so launcher.py dispatches
  to NMListener's main() inside the same bundle.
"""

import collections
import json
import os
import subprocess
import threading
from typing import Optional

import bundle_paths

LOG_MAXLEN = 600   # lines kept in the ring buffer (raised for debugging)

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
        env.update(bundle_paths.listener_env())
        _proc = subprocess.Popen(
            bundle_paths.listener_cmd(),
            cwd=bundle_paths.listener_source_dir(),
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


def get_runtime_info() -> dict:
    """
    Return the runtime info written by NMListener on connect:
      my_node_id, my_node_short, channel_num, frequency_mhz
    Returns empty dict if the file doesn't exist or can't be read.
    """
    runtime_file = os.path.join(bundle_paths.data_dir(), "listener_runtime.json")
    try:
        with open(runtime_file) as f:
            return json.load(f)
    except Exception:
        return {}


def get_reference_telemetry() -> dict:
    """
    Return the reference node's latest cached telemetry written by NMListener
    (reference_telemetry.json): battery_level, voltage, temperature,
    relative_humidity, barometric_pressure, gas_resistance, rx_time.
    Returns empty dict if the file doesn't exist or can't be read.
    """
    tele_file = os.path.join(bundle_paths.data_dir(), "reference_telemetry.json")
    try:
        with open(tele_file) as f:
            return json.load(f)
    except Exception:
        return {}




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
