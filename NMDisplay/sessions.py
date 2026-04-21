"""
sessions.py — Session management for NodeMonitor.

A "session" is a named capture context: one SQLite DB + one connection config.
Sessions are indexed in NMData/sessions.json.
The active session is written to NMData/active.json, which both NMListener
and NMDisplay read to know which DB to use and how to connect.

First run: auto-creates a "Default" session pointing to the legacy
nodemonitor.db if it exists, or a fresh DB otherwise.
"""

import json
import os
import re
import time
from typing import Optional

import bundle_paths

NMDATA_DIR    = bundle_paths.data_dir()
SESSIONS_FILE = os.path.join(NMDATA_DIR, "sessions.json")
ACTIVE_FILE   = os.path.join(NMDATA_DIR, "active.json")

_DEFAULT_CONNECTION = {"type": "tcp", "host": "192.168.1.25", "port": 4403}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def load_all() -> list[dict]:
    """Return all sessions. Bootstraps from legacy DB on first run."""
    os.makedirs(NMDATA_DIR, exist_ok=True)
    if not os.path.exists(SESSIONS_FILE):
        return _bootstrap()
    with open(SESSIONS_FILE) as f:
        return json.load(f)


def get_active() -> dict:
    """Return the active session dict (guaranteed to exist after first call)."""
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE) as f:
            return json.load(f)
    # First call: bootstrap and activate first session
    sessions = load_all()
    return _write_active(sessions[0])


def get_db_path() -> str:
    """Return the absolute path to the active session's SQLite DB."""
    active = get_active()
    return active.get("db_path") or os.path.join(NMDATA_DIR, active["db"])


def create(name: str, connection: dict, seed_on_connect: bool = True,
           save_packet_data: bool = True) -> dict:
    """Create a new session, activate it, and return it."""
    sessions = load_all()
    session = _make_session(name, connection, seed_on_connect, save_packet_data=save_packet_data)
    sessions.append(session)
    _save_all(sessions)
    return _write_active(session)


def patch_active_flag(key: str, value) -> None:
    """Update a single flag in active.json (without touching sessions.json).
    Used for mid-session toggles like save_packet_data."""
    active = get_active()
    active[key] = value
    tmp = ACTIVE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(active, f, indent=2)
    os.replace(tmp, ACTIVE_FILE)


def activate(session_id: str, connection_override: Optional[dict] = None) -> dict:
    """Switch the active session. Returns the activated session.
    If connection_override is provided the session's stored connection is
    updated in sessions.json so the next activation remembers it."""
    sessions = load_all()
    now = int(time.time())
    target = None
    for s in sessions:
        if s["id"] == session_id:
            s["last_active"] = now
            if connection_override:
                s["connection"] = connection_override
            target = s
    if target is None:
        raise ValueError(f"Session not found: {session_id}")
    _save_all(sessions)
    return _write_active(target)


def delete(session_id: str) -> None:
    """Delete a session. Raises ValueError if it's the active session."""
    active = get_active()
    if active["id"] == session_id:
        raise ValueError("Cannot delete the active session.")
    sessions = [s for s in load_all() if s["id"] != session_id]
    _save_all(sessions)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _bootstrap() -> list[dict]:
    legacy_db = os.path.join(NMDATA_DIR, "nodemonitor.db")
    db_file = "nodemonitor.db" if os.path.exists(legacy_db) else None
    session = _make_session("Default", _DEFAULT_CONNECTION, seed_on_connect=True, db=db_file)
    sessions = [session]
    _save_all(sessions)
    _write_active(session)
    return sessions


def _make_session(name: str, connection: dict, seed_on_connect: bool,
                  db: Optional[str] = None, save_packet_data: bool = True) -> dict:
    ts = int(time.time())
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    sid = f"{slug}_{ts}"
    return {
        "id": sid,
        "name": name,
        "db": db or f"{sid}.db",
        "connection": connection,
        "seed_on_connect": seed_on_connect,
        "save_packet_data": save_packet_data,
        "created": ts,
        "last_active": ts,
    }


def _save_all(sessions: list[dict]) -> None:
    os.makedirs(NMDATA_DIR, exist_ok=True)
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sessions, f, indent=2)
    os.replace(tmp, SESSIONS_FILE)


def _write_active(session: dict) -> dict:
    """Enrich session with abs db_path, write to active.json, return it."""
    active = dict(session)
    active["db_path"] = os.path.join(NMDATA_DIR, session["db"])
    os.makedirs(NMDATA_DIR, exist_ok=True)
    tmp = ACTIVE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(active, f, indent=2)
    os.replace(tmp, ACTIVE_FILE)
    return active
