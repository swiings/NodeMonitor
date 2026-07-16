"""
config.py — NMListener runtime configuration.

Reads NMData/active.json (written by NMDisplay/sessions.py) so that session
and connection settings are shared between the two apps. If active.json is
absent (standalone operation), falls back to the hardcoded TCP defaults below.

reload() re-reads active.json at runtime so that flags like save_packet_data
can be toggled mid-session from the NMDisplay UI.
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

# ── Packet persistence ───────────────────────────────────────────────
# When False, raw packet rows are NOT written to the packets table.
# Nodes, timeline buckets, and in-memory stats continue to update so
# the live display stays current.  Can be toggled mid-session by the UI
# (NMDisplay patches active.json; reload() picks up the change within 5 s).
SAVE_PACKET_DATA: bool = bool(_active.get("save_packet_data", True))

# ── Reconnect backoff ────────────────────────────────────────────────
RECONNECT_DELAY_INITIAL = 5    # seconds
RECONNECT_DELAY_MAX     = 60   # seconds

# ── Console output ───────────────────────────────────────────────────
PACKET_LOG_VERBOSE = True

# ── Movement detection (position packets) ──────────────────────────
# Movement classification uses 2D lat/lon only (horizontal Haversine).
# Altitude is intentionally ignored to avoid false movement from smart-positioning altitude jitter.
MOTION_LOOKBACK_S = 2 * 3600
MOTION_MAX_SAMPLES = 120
MOTION_RECENT_WINDOW_S = 30 * 60
MOTION_MAX_FIX_AGE_S = 15 * 60
MOTION_JITTER_METERS = 25.0
MOTION_MIN_RECENT_SAMPLES = 4
MOTION_MAX_MEDIAN_INTERVAL_S = 8 * 60
MOTION_MIN_MOVING_STEPS = 2
MOTION_MIN_PATH_METERS = 120.0
MOTION_MIN_NET_METERS = 60.0
MOTION_MIN_SCORE = 4
MOTION_MIN_SATS = 4
MOTION_MIN_PRECISION_BITS = 8
MOTION_VEHICLE_TIMEOUT_S = 3600


def reload() -> None:
    """Re-read active.json and update mutable runtime flags."""
    global SAVE_PACKET_DATA
    try:
        fresh = load_active()
        SAVE_PACKET_DATA = bool(fresh.get("save_packet_data", True))
    except Exception:
        pass
