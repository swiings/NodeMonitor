"""
geocode.py — Reverse geocoding via Google Maps Geocoding API.

Background thread scans for nodes with GPS coordinates but no address,
calls the API, and writes street/city/state back to the DB.

Nodes with lat/lon and street=NULL are candidates. On failure (no result
or API error), all three fields are set to "" to avoid retrying forever.
Transient network errors leave fields as NULL so the next pass retries.
"""

import sqlite3
import threading
import time

import requests

import sessions as _sessions


def _reverse_geocode(lat: float, lon: float, api_key: str) -> tuple:
    """Return (street, city, state) for coordinates, or (None, None, None) on transient error."""
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": api_key},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        print(f"[geocode] request error: {e}")
        return None, None, None  # transient — will retry next pass

    if data.get("status") != "OK" or not data.get("results"):
        return "", "", ""  # no address exists — don't retry

    components = data["results"][0].get("address_components", [])
    parts: dict = {}
    for c in components:
        for t in c["types"]:
            if t not in parts:
                parts[t] = c

    street_number = parts.get("street_number", {}).get("long_name", "")
    route         = parts.get("route", {}).get("short_name", "")
    city          = parts.get("locality", {}).get("long_name", "")
    state         = parts.get("administrative_area_level_1", {}).get("short_name", "")

    street = f"{street_number} {route}".strip()
    return street, city, state


def _run_pass(api_key: str) -> None:
    db_path = _sessions.get_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT node_id, lat, lon FROM nodes "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL AND street IS NULL"
        ).fetchall()
        for node_id, lat, lon in rows:
            street, city, state = _reverse_geocode(lat, lon, api_key)
            if street is None:
                continue  # transient error — leave NULL, retry next pass
            conn.execute(
                "UPDATE nodes SET street=?, city=?, state=? WHERE node_id=?",
                (street, city, state, node_id),
            )
            conn.commit()
            time.sleep(1)  # ~1 QPS — well within Google's limits
    except Exception as e:
        print(f"[geocode] pass error: {e}")
    finally:
        conn.close()


def _loop(api_key: str) -> None:
    while True:
        try:
            _run_pass(api_key)
        except Exception as e:
            print(f"[geocode] unexpected error: {e}")
        time.sleep(60)


def start(api_key: str) -> None:
    """Start the background geocoding thread. No-op if api_key is empty."""
    if not api_key:
        return
    t = threading.Thread(target=_loop, args=(api_key,), daemon=True, name="geocode-bg")
    t.start()
    print("[geocode] background thread started")
