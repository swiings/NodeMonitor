"""
geocode.py - Reverse geocoding via Google Maps Geocoding API.

Background thread scans for nodes with GPS coordinates but no address,
calls the API, and writes street/city/state back to the DB.

Nodes with lat/lon and blank address fields are candidates. Only confirmed
ZERO_RESULTS responses are cached as blank fields; API/auth/quota failures are
treated as transient so they can recover after config fixes.
"""

import sqlite3
import threading
import time

import requests

import sessions as _sessions


def _is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


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

    status = data.get("status")
    if status == "ZERO_RESULTS":
        return "", "", ""  # confirmed no address for this point

    if status != "OK" or not data.get("results"):
        err = data.get("error_message") or ""
        print(f"[geocode] API status {status or 'UNKNOWN'}: {err}")
        return None, None, None

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
            "WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "AND ("
            "TRIM(COALESCE(street, '')) = '' "
            "OR TRIM(COALESCE(city, '')) = '' "
            "OR TRIM(COALESCE(state, '')) = ''"
            ")"
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


def geocode_node(node_id: int, api_key: str, force: bool = False) -> dict:
    """Geocode one node on demand. Returns a structured status payload."""
    if not api_key:
        return {"ok": False, "reason": "no_api_key"}

    db_path = _sessions.get_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT lat, lon, street, city, state FROM nodes WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "not_found"}

        lat, lon, street, city, state = row
        if lat is None or lon is None:
            return {"ok": False, "reason": "no_position"}

        has_address = not (_is_blank(street) and _is_blank(city) and _is_blank(state))
        if has_address and not force:
            return {
                "ok": True,
                "status": "cached",
                "address": {
                    "street": street or "",
                    "city": city or "",
                    "state": state or "",
                },
            }

        new_street, new_city, new_state = _reverse_geocode(float(lat), float(lon), api_key)
        if new_street is None:
            return {"ok": False, "reason": "transient_error"}

        conn.execute(
            "UPDATE nodes SET street=?, city=?, state=? WHERE node_id=?",
            (new_street, new_city, new_state, node_id),
        )
        conn.commit()
        return {
            "ok": True,
            "status": "updated",
            "address": {
                "street": new_street or "",
                "city": new_city or "",
                "state": new_state or "",
            },
        }
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
        print("[geocode] not started: GOOGLE_MAPS_API_KEY is empty")
        return
    t = threading.Thread(target=_loop, args=(api_key,), daemon=True, name="geocode-bg")
    t.start()
    print("[geocode] background thread started")
