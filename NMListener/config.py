"""
config.py — NMListener runtime configuration.

Reads NMData/active.json (written by NMDisplay/sessions.py) so that session
and connection settings are shared between the two apps. If active.json is
absent (standalone operation), falls back to the hardcoded TCP defaults below.
"""

import os
from connection import load_active

_here = os.path.dirname(os.path.abspath(__file__))

# Load active session at startup
_active = load_active()
_conn   = _active.get("connection", {})

# ── Connection (from active session, or hardcoded fallback) ──────────
CONNECTION = _conn if _conn else {"type": "tcp", "host": "192.168.1.25", "port": 4403}

# Legacy flat attributes still used in a few places
TCP_HOST = CONNECTION.get("host", "192.168.1.25")
TCP_PORT = int(CONNECTION.get("port", 4403))

# ── Database path ────────────────────────────────────────────────────
DB_PATH = (
    _active.get("db_path")
    or os.path.join(_here, "..", "NMData", "nodemonitor.db")
)

# ── Seeding ──────────────────────────────────────────────────────────
# Whether to load the node list from the device on connect.
# Stored per-session in active.json; defaults to True.
SEED_ON_CONNECT = _active.get("seed_on_connect", True)

# ── Reconnect backoff ────────────────────────────────────────────────
RECONNECT_DELAY_INITIAL = 5    # seconds
RECONNECT_DELAY_MAX     = 60   # seconds

# ── Console output ───────────────────────────────────────────────────
PACKET_LOG_VERBOSE = True
