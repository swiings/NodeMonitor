#!/usr/bin/env python3
"""
device_discovery.py — Runtime USB serial device identification.

Shared module for ChanScan and NodeMonitor.  Scans /dev/ttyACM* and
/dev/ttyUSB* to identify:

  SNAX  — Heltec ESP32 V4 running Meshtastic firmware.
           Identified by a successful Meshtastic library handshake.

  HAXX  — Heltec ESP32 V4 running custom RadioLib/ChanScan firmware.
           Identified by a plaintext banner or response on connect.

The two probes run sequentially per port:
  1. HAXX raw-text probe first (fast, 2 s max, no library side-effects).
  2. Meshtastic probe only on ports that didn't respond as HAXX (4 s max).

A file-lock + /tmp cache serialises concurrent calls so that ChanScan and
NodeMonitor starting simultaneously don't fight over ports.

Usage:
    from device_discovery import discover
    devices = discover()
    # {"snax": "/dev/ttyACM1", "haxx": "/dev/ttyACM0"}
    # Values are None if that device was not found.

Environment overrides:
    HAXX_SIGNATURE          substring to look for in HAXX banner (default: "HAXX")
                            set to "" to accept *any* printable-text response
    MESHTASTIC_TIMEOUT_SECS seconds to wait for Meshtastic handshake (default: 4)
    HAXX_LISTEN_SECS        seconds to listen for HAXX banner (default: 2)
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
MESHTASTIC_TIMEOUT = float(os.environ.get("MESHTASTIC_TIMEOUT_SECS", "4"))
HAXX_LISTEN        = float(os.environ.get("HAXX_LISTEN_SECS", "2"))
# Substring to find in the HAXX device's serial output.
# Set HAXX_SIGNATURE="" to treat any printable-text response as HAXX.
HAXX_SIGNATURE     = os.environ.get("HAXX_SIGNATURE", "HAXX")

# ── Inter-process result cache ────────────────────────────────────────────────
_LOCK_FILE   = "/tmp/device_discovery.lock"
_RESULT_FILE = "/tmp/device_discovery_result.json"
_RESULT_TTL  = 30   # seconds — how long to trust a cached result


# ── Port enumeration ──────────────────────────────────────────────────────────

def _scan_ports() -> list[str]:
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


# ── HAXX probe ────────────────────────────────────────────────────────────────

def _probe_haxx(port: str) -> bool:
    """
    Open port at BAUD_RATE, send a bare newline to elicit a response, then
    read lines for up to HAXX_LISTEN seconds.

    Returns True if a line containing HAXX_SIGNATURE is received (or if
    HAXX_SIGNATURE is empty and any printable-ASCII line is received).

    Meshtastic devices speak binary protobuf and won't emit printable text
    in response to a stray newline, so False here reliably means "not HAXX".
    """
    try:
        with serial.Serial(port, BAUD_RATE, timeout=0.4) as ser:
            ser.reset_input_buffer()
            ser.write(b"\r\n")          # prod firmware for a banner/prompt
            deadline = time.monotonic() + HAXX_LISTEN
            while time.monotonic() < deadline:
                raw = ser.readline()
                if not raw:
                    ser.write(b"\r\n")  # nudge again if quiet
                    continue
                text = raw.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                # Reject lines that contain non-printable bytes (binary protocol)
                if not all(0x20 <= ord(c) < 0x7F or c in "\t" for c in text):
                    log.debug("HAXX probe [%s]: binary data — not HAXX", port)
                    return False
                log.debug("HAXX probe [%s]: %r", port, text)
                sig = HAXX_SIGNATURE.strip()
                if not sig or sig.lower() in text.lower():
                    return True
    except serial.SerialException as exc:
        log.debug("HAXX probe could not open %s: %s", port, exc)
    except Exception as exc:
        log.debug("HAXX probe error on %s: %s", port, exc)
    return False


# ── Meshtastic probe ──────────────────────────────────────────────────────────

def _probe_meshtastic(port: str) -> bool:
    """
    Attempt a Meshtastic SerialInterface connection on port in a daemon thread.
    Returns True if the handshake completes within MESHTASTIC_TIMEOUT seconds.

    Only called on ports that didn't respond to the HAXX probe, so we avoid
    running this on the HAXX device (which would leave an orphaned thread
    holding the port).
    """
    result   = [False]
    iface_ref = [None]

    def _attempt():
        # Suppress meshtastic's verbose startup logging during the probe
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
        log.debug(
            "Meshtastic probe timed out on %s (background thread will clean up)", port
        )
    return result[0]


# ── Core scan logic ───────────────────────────────────────────────────────────

def _run_discovery() -> dict:
    """Scan all ports and return {"snax": path_or_None, "haxx": path_or_None}."""
    ports = _scan_ports()
    log.info("[discovery] Scanning %d port(s): %s", len(ports), ports)

    result: dict = {"snax": None, "haxx": None}

    for port in ports:
        if result["snax"] is not None and result["haxx"] is not None:
            break

        log.info("[discovery] Probing %s …", port)

        # Step 1: HAXX raw-text probe (fast, no library side-effects)
        if result["haxx"] is None:
            if _probe_haxx(port):
                log.info("[discovery] HAXX (ChanScan)   → %s", port)
                result["haxx"] = port
                continue        # definitely not Meshtastic; skip step 2

        # Step 2: Meshtastic probe (only on ports that didn't answer as HAXX)
        if result["snax"] is None and result["haxx"] != port:
            if _probe_meshtastic(port):
                log.info("[discovery] SNAX (Meshtastic) → %s", port)
                result["snax"] = port

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
        sig = HAXX_SIGNATURE or "<any text>"
        raise RuntimeError(
            "HAXX (ChanScan node) not found on any serial port. "
            "Check USB connection and that the node is powered on. "
            f"Expected banner containing {sig!r}; "
            "set HAXX_SIGNATURE env var to override."
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
