#!/usr/bin/env python3
"""
device_discovery.py — Runtime USB serial device identification.

Shared module for ChanScan and NodeMonitor.  Scans /dev/ttyACM* and
/dev/ttyUSB* to identify:

  SNAX  — Heltec ESP32 V4 running Meshtastic firmware.
           Identified by a successful Meshtastic library handshake.

  HAXX  — Heltec ESP32 V4 running custom RadioLib/ChanScan firmware.
           Identified by elimination: accessible port that is not SNAX.

Algorithm (two phases):
  Phase 1 — Meshtastic probe each port (up to MESHTASTIC_TIMEOUT_SECS each).
             First port that passes → SNAX.
  Phase 2 — After a brief grace period (HAXX_GRACE_SECS) to let any
             Meshtastic probe threads release ports, any remaining accessible
             port → HAXX.

A file-lock + /tmp cache serialises concurrent calls so that ChanScan and
NodeMonitor starting simultaneously don't fight over ports.

Usage:
    from device_discovery import discover
    devices = discover()
    # {"snax": "/dev/ttyACM1", "haxx": "/dev/ttyACM0"}
    # Values are None if that device was not found.

Environment overrides:
    MESHTASTIC_TIMEOUT_SECS  seconds to wait for Meshtastic handshake (default: 5)
    HAXX_GRACE_SECS          seconds to wait after probes before checking HAXX
                             port accessibility (default: 1)
"""

import fcntl
import glob
import json
import logging
import os
import threading
import time

import serial

log = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────
BAUD_RATE          = 115200
MESHTASTIC_TIMEOUT = float(os.environ.get("MESHTASTIC_TIMEOUT_SECS", "5"))
HAXX_GRACE         = float(os.environ.get("HAXX_GRACE_SECS", "1"))

# ── Inter-process result cache ────────────────────────────────────────────────
_LOCK_FILE   = "/tmp/device_discovery.lock"
_RESULT_FILE = "/tmp/device_discovery_result.json"
_RESULT_TTL  = 30   # seconds — how long to trust a cached result


# ── Port enumeration ──────────────────────────────────────────────────────────

def _scan_ports() -> list[str]:
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


# ── Meshtastic probe ──────────────────────────────────────────────────────────

def _probe_meshtastic(port: str) -> bool:
    """
    Attempt a Meshtastic SerialInterface connection on port in a daemon thread.
    Returns True if the handshake completes within MESHTASTIC_TIMEOUT seconds.

    The thread is always daemonised so it can never block process exit, but
    after a timeout we sleep HAXX_GRACE_SECS before touching the port again
    so the thread has time to clean up and release it.
    """
    result    = [False]
    iface_ref = [None]

    def _attempt():
        mesh_log = logging.getLogger("meshtastic")
        orig_level = mesh_log.level
        mesh_log.setLevel(logging.CRITICAL)
        try:
            from meshtastic.serial_interface import SerialInterface
            iface = SerialInterface(port)
            iface_ref[0] = iface
            result[0] = True
        except Exception as exc:
            log.debug("Meshtastic probe failed on %s: %s", port, exc)
        finally:
            mesh_log.setLevel(orig_level)
            iface = iface_ref[0]
            if iface is not None:
                try:
                    iface.close()
                except Exception:
                    pass

    t = threading.Thread(target=_attempt, daemon=True)
    t.start()
    t.join(timeout=MESHTASTIC_TIMEOUT)
    if t.is_alive():
        log.debug("Meshtastic probe timed out on %s", port)
    return result[0]


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
      Phase 1: Meshtastic-probe each port to find SNAX.
      Phase 2: After a grace period, any remaining accessible port is HAXX.
    """
    ports = _scan_ports()
    log.info("[discovery] Scanning %d port(s): %s", len(ports), ports)

    result: dict = {"snax": None, "haxx": None}
    probed_ports: list[str] = []

    # ── Phase 1: identify SNAX via Meshtastic handshake ──────────────────
    for port in ports:
        probed_ports.append(port)
        log.info("[discovery] Meshtastic probe on %s …", port)
        if _probe_meshtastic(port):
            log.info("[discovery] SNAX (Meshtastic) → %s", port)
            result["snax"] = port
            break   # found it; no need to probe remaining ports

    # ── Grace period: let any timed-out probe threads release their ports ─
    if HAXX_GRACE > 0:
        log.debug("[discovery] Grace period %.1fs …", HAXX_GRACE)
        time.sleep(HAXX_GRACE)

    # ── Phase 2: first accessible non-SNAX port is HAXX ──────────────────
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
