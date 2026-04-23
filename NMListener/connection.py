"""
connection.py — Meshtastic interface factory.

Reads NMData/active.json (written by NMDisplay/sessions.py) to get the
connection config. Falls back to hardcoded TCP defaults if the file is absent
so NMListener can still be run standalone without NMDisplay.

Supported connection types:
  tcp    — TCP/IP to a node's network port (home, fixed IP)
  serial — USB/serial connection to a node plugged into this machine
  ble    — Bluetooth LE connection to a nearby node
"""

import json
import logging
import os
import sys
from typing import Optional

log = logging.getLogger(__name__)


class FatalConnectionError(Exception):
    """Raised for configuration errors that will not resolve by retrying."""

_here = os.path.dirname(os.path.abspath(__file__))
# NM_DATA_DIR is set by NMDisplay when running from a frozen .app bundle;
# in dev mode it is not set, so fall back to the relative NMData path.
_nm_data_dir = os.environ.get("NM_DATA_DIR") or os.path.abspath(
    os.path.join(_here, "..", "NMData")
)
ACTIVE_FILE = os.path.join(_nm_data_dir, "active.json")

_FALLBACK_CONNECTION = {"type": "tcp", "host": "192.168.1.25", "port": 4403}

# Cache the discovered serial port for the lifetime of this process so that
# reconnect loops don't re-probe (which races with the port still closing).
_discovered_serial_port: str | None = None


def load_active() -> dict:
    """Load NMData/active.json. Returns empty dict if not present."""
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE) as f:
            return json.load(f)
    return {}


def make_interface(conn_cfg: dict):
    """
    Instantiate a Meshtastic interface from a connection config dict.

    TCP:
      {"type": "tcp", "host": "192.168.1.25", "port": 4403}

    Serial (USB):
      {"type": "serial"}                       — auto-detect first device
      {"type": "serial", "dev": "/dev/tty.usbserial-0001"}

    BLE:
      {"type": "ble"}                          — auto-detect first device
      {"type": "ble", "address": "AA:BB:CC:DD:EE:FF"}
    """
    conn_type = conn_cfg.get("type", "tcp")

    if conn_type == "tcp":
        from meshtastic.tcp_interface import TCPInterface
        host = conn_cfg.get("host", "localhost")
        port = int(conn_cfg.get("port", 4403))
        return TCPInterface(host, portNumber=port)

    elif conn_type == "serial":
        from meshtastic.serial_interface import SerialInterface
        global _discovered_serial_port
        dev = conn_cfg.get("dev") or None   # None → auto-detect

        if dev is None:
            # Use the cached port from a previous successful discovery so that
            # reconnect loops don't re-probe (probing races with the previous
            # connection still closing and can pick the wrong port).
            if _discovered_serial_port is not None:
                dev = _discovered_serial_port
                log.info("Serial: reusing cached SNAX port %s", dev)
            else:
                # First connect: run device discovery to find SNAX specifically
                # when multiple USB serial devices are plugged in.
                try:
                    from device_discovery import discover
                    _found = discover().get("snax")
                    if _found:
                        dev = _found
                        _discovered_serial_port = dev
                        log.info("Serial auto-detect via discovery: SNAX at %s", dev)
                    else:
                        log.warning(
                            "Device discovery did not find SNAX; "
                            "falling back to first available serial port"
                        )
                except Exception as _exc:
                    log.warning("Device discovery unavailable (%s); using fallback", _exc)

            if dev is None:
                # Fallback: first ttyACM/ttyUSB port (excludes Pi internal UARTs)
                try:
                    import serial.tools.list_ports
                    ports = [
                        p.device for p in serial.tools.list_ports.comports()
                        if p.device.startswith(("/dev/ttyACM", "/dev/ttyUSB"))
                    ]
                    if ports:
                        dev = ports[0]
                        _discovered_serial_port = dev
                        log.info("Serial fallback: selected %s", dev)
                except Exception:
                    pass  # SerialInterface will raise a clear error

        return SerialInterface(dev)

    elif conn_type == "ble":
        from meshtastic.ble_interface import BLEInterface
        address = conn_cfg.get("address") or None   # None → auto-detect

        if address is None:
            # Pre-scan so we can give a useful error when multiple devices are found.
            devices = BLEInterface.scan()
            if len(devices) == 0:
                raise FatalConnectionError(
                    "No Meshtastic BLE devices found in range."
                )
            elif len(devices) == 1:
                address = devices[0].address
                log.info("BLE auto-detect: selected %s [%s]", devices[0].name, address)
            else:
                names = ", ".join(
                    f"{d.name or 'unnamed'} [{d.address}]" for d in devices
                )
                raise FatalConnectionError(
                    f"Multiple Meshtastic BLE devices found — edit the session and "
                    f"specify one address: {names}"
                )

        return BLEInterface(address)

    else:
        raise ValueError(f"Unknown connection type: {conn_type!r}. "
                         f"Expected 'tcp', 'serial', or 'ble'.")


def summary(conn_cfg: dict) -> str:
    """Human-readable one-liner for logging/display."""
    t = conn_cfg.get("type", "tcp")
    if t == "tcp":
        return f"TCP {conn_cfg.get('host', '?')}:{conn_cfg.get('port', 4403)}"
    elif t == "serial":
        return f"Serial {conn_cfg.get('dev') or 'auto-detect'}"
    elif t == "ble":
        return f"BLE {conn_cfg.get('address') or 'auto-detect'}"
    return t.upper()
