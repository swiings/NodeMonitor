#!/usr/bin/env python3
"""
device_discovery.py — Runtime USB serial device identification.

Shared module for ChanScan and NodeMonitor.  Scans /dev/ttyACM* and
/dev/ttyUSB* to identify:

  SNAX  — Heltec ESP32 V4 running Meshtastic firmware.
           Identified by running `meshtastic --serial <port> --info`
           and checking for a valid Owner line in the output.

  HAXX  — Heltec ESP32 V4 running custom RadioLib/ChanScan firmware.
           Identified by elimination: accessible port that is not SNAX.

Algorithm (two phases):
  Phase 1 — Run `meshtastic --serial <port> --info` (subprocess, hard timeout)
             on each port until one succeeds → SNAX.
  Phase 2 — Any remaining accessible port → HAXX.

Using subprocess for the Meshtastic probe avoids orphaned Python threads
that can hold a serial port open after a timeout.

A file-lock + /tmp cache serialises concurrent calls so that ChanScan and
NodeMonitor starting simultaneously don't fight over ports.

Usage:
    from device_discovery import discover
    devices = discover()
    # {"snax": "/dev/ttyACM1", "haxx": "/dev/ttyACM0"}
    # Values are None if that device was not found.

Environment overrides:
    MESHTASTIC_TIMEOUT_SECS  seconds to wait for meshtastic --info (default: 8)
    HAXX_GRACE_SECS          pause after probes before checking HAXX
                             port accessibility (default: 0.5)
"""

import fcntl
import glob
import json
import logging
import os
import shutil
import subprocess
import time

import serial

log = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────
BAUD_RATE          = 115200
MESHTASTIC_TIMEOUT = float(os.environ.get("MESHTASTIC_TIMEOUT_SECS", "8"))
HAXX_GRACE         = float(os.environ.get("HAXX_GRACE_SECS", "0.5"))

# ── Inter-process result cache ────────────────────────────────────────────────
_LOCK_FILE   = "/tmp/device_discovery.lock"
_RESULT_FILE = "/tmp/device_discovery_result.json"
_RESULT_TTL  = 300  # seconds — 5 min; long enough for both apps to share

# ── Well-known meshtastic CLI locations (checked in order) ───────────────────
# The CLI ships with the meshtastic Python package.  NodeMonitor installs it
# in its own venv; ChanScan doesn't, so we also look at common Pi paths.
_MESHTASTIC_CANDIDATES = [
    "/home/haxx/.venv-nodemonitor/bin/meshtastic",
    "/home/pi/.venv/bin/meshtastic",
]


# ── Port enumeration ──────────────────────────────────────────────────────────

def _scan_ports() -> list[str]:
    # ttyAMA* are Pi internal UARTs (Bluetooth etc.) — exclude them.
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def _find_meshtastic_cli() -> str | None:
    """Return path to the meshtastic CLI, or None if not found."""
    cmd = shutil.which("meshtastic")
    if cmd:
        return cmd
    for path in _MESHTASTIC_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# ── Meshtastic probe ──────────────────────────────────────────────────────────

def _probe_meshtastic(port: str, cli: str) -> bool:
    """
    Run `meshtastic --serial <port> --info` as a subprocess with a hard
    timeout.  Returns True if the output contains a valid Owner line.

    Because this is a subprocess, the port is fully released the instant
    the process exits or is killed — no orphaned threads, no lingering holds.
    """
    try:
        result = subprocess.run(
            [cli, "--serial", port, "--info"],
            capture_output=True,
            text=True,
            timeout=MESHTASTIC_TIMEOUT,
        )
        output = result.stdout + result.stderr
        log.debug(
            "Meshtastic probe [%s]: rc=%d  out=%r",
            port, result.returncode, output[:300],
        )
        # A successfully connected Meshtastic node always shows an Owner line
        return "Owner:" in output
    except subprocess.TimeoutExpired:
        log.debug("Meshtastic probe timed out on %s", port)
        return False
    except Exception as exc:
        log.debug("Meshtastic probe error on %s: %s", port, exc)
        return False


# ── HAXX accessibility check ──────────────────────────────────────────────────

def _port_accessible(port: str) -> bool:
    """Returns True if the port can be opened at BAUD_RATE without error."""
    try:
        with serial.Serial(port, BAUD_RATE, timeout=0.1):
            return True
    except serial.SerialException as exc:
        log.debug("Port %s not accessible: %s", port, exc)
        return False


# ── Core scan logic ───────────────────────────────────────────────────────────

def _run_discovery() -> dict:
    """
    Two-phase scan:
      Phase 1: meshtastic --info each port to identify SNAX.
      Phase 2: first remaining accessible port is HAXX.
    """
    ports = _scan_ports()
    log.info("[discovery] Scanning %d port(s): %s", len(ports), ports)

    result: dict = {"snax": None, "haxx": None}

    cli = _find_meshtastic_cli()
    if cli:
        log.info("[discovery] Using meshtastic CLI: %s", cli)
    else:
        log.warning(
            "[discovery] meshtastic CLI not found — SNAX identification "
            "will be skipped; HAXX identified as first accessible port"
        )

    # ── Phase 1: identify SNAX ────────────────────────────────────────────
    if cli:
        for port in ports:
            log.info("[discovery] Meshtastic probe on %s …", port)
            if _probe_meshtastic(port, cli):
                log.info("[discovery] SNAX (Meshtastic) → %s", port)
                result["snax"] = port
                break

    # Brief pause so any OS-level port state settles before we try to open
    if HAXX_GRACE > 0:
        time.sleep(HAXX_GRACE)

    # ── Phase 2: HAXX = first accessible non-SNAX port ───────────────────
    for port in ports:
        if port == result["snax"]:
            continue
        if _port_accessible(port):
            log.info("[discovery] HAXX (ChanScan, by elimination) → %s", port)
            result["haxx"] = port
            break

    log.info("[discovery] Result: %s", result)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def discover(
    require_snax: bool = False,
    require_haxx: bool = False,
) -> dict:
    """
    Discover SNAX and HAXX serial devices.

    Results are cached in /tmp for _RESULT_TTL seconds and shared across
    processes via a file lock so ChanScan and NodeMonitor starting at the
    same time don't race over the serial ports during probing.

    Args:
        require_snax: raise RuntimeError if SNAX is not found.
        require_haxx: raise RuntimeError if HAXX is not found.

    Returns:
        {"snax": "/dev/ttyACMx" or None, "haxx": "/dev/ttyACMy" or None}
    """
    # ── Fast path: valid cached result ───────────────────────────────────
    if os.path.exists(_RESULT_FILE):
        try:
            age = time.time() - os.path.getmtime(_RESULT_FILE)
            if age < _RESULT_TTL:
                with open(_RESULT_FILE) as f:
                    cached = json.load(f)
                log.info("[discovery] Cache hit (age=%.1fs): %s", age, cached)
                _check(cached, require_snax, require_haxx)
                return cached
        except Exception:
            pass    # stale or corrupt; fall through

    # ── Serialise across processes ────────────────────────────────────────
    lock_fd = open(_LOCK_FILE, "w")
    try:
        first_in = True
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            first_in = False
            log.info("[discovery] Waiting for another process to finish probing …")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Second arrival: try reading the cache the first process just wrote
        if not first_in and os.path.exists(_RESULT_FILE):
            try:
                age = time.time() - os.path.getmtime(_RESULT_FILE)
                if age < _RESULT_TTL:
                    with open(_RESULT_FILE) as f:
                        cached = json.load(f)
                    log.info("[discovery] Second-process cache hit: %s", cached)
                    _check(cached, require_snax, require_haxx)
                    return cached
            except Exception:
                pass

        # We hold the lock — run full discovery and write cache
        result = _run_discovery()
        try:
            with open(_RESULT_FILE, "w") as f:
                json.dump(result, f)
        except Exception as exc:
            log.warning("[discovery] Could not write cache: %s", exc)

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    _check(result, require_snax, require_haxx)
    return result


def _check(result: dict, require_snax: bool, require_haxx: bool) -> None:
    if require_snax and not result.get("snax"):
        raise RuntimeError(
            "SNAX (Meshtastic node) not found on any serial port. "
            "Check USB connection and that the node is powered on."
        )
    if require_haxx and not result.get("haxx"):
        raise RuntimeError(
            "HAXX (ChanScan node) not found on any serial port. "
            "Check USB connection and that the node is powered on."
        )


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    for _f in (_LOCK_FILE, _RESULT_FILE):
        try:
            os.unlink(_f)
        except FileNotFoundError:
            pass

    print("Running device discovery …\n")
    devices = discover()
    print("\nResult:")
    print(f"  SNAX (Meshtastic) : {devices['snax'] or 'not found'}")
    print(f"  HAXX (ChanScan)   : {devices['haxx'] or 'not found'}")
