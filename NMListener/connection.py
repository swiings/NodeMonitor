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
import os
from typing import Optional

_here = os.path.dirname(os.path.abspath(__file__))
ACTIVE_FILE = os.path.abspath(os.path.join(_here, "..", "NMData", "active.json"))

_FALLBACK_CONNECTION = {"type": "tcp", "host": "192.168.1.25", "port": 4403}


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
        dev = conn_cfg.get("dev") or None   # None → auto-detect
        return SerialInterface(dev)

    elif conn_type == "ble":
        from meshtastic.ble_interface import BLEInterface
        address = conn_cfg.get("address") or None   # None → auto-detect
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
