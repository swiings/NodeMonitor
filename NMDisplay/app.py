"""
app.py — NMDisplay Flask server.

Run:
    python3 app.py

Serves the browser display at https://localhost:5000
Reads the active session DB (read-only; WAL mode allows concurrent access with NMListener).
"""

import csv
import io
import ipaddress
import os
import re
import socket
import time

import requests as req_lib
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

import bundle_paths
import geocode as geocode_mod
import listener_proc
import queries
import sessions as sess_mod
import settings as cfg_mod

def _get_lan_ip() -> str:
    """Best-effort: return the LAN IPv4 of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _ensure_ssl_cert() -> tuple[str, str]:
    """
    Return (cert_path, key_path). Generates a 10-year self-signed cert on
    first run; subsequent runs reuse the existing files so the browser only
    needs to accept it once.
    """
    cert_path = os.path.join(bundle_paths.display_dir(), "cert.pem")
    key_path  = os.path.join(bundle_paths.display_dir(), "key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    lan_ip = _get_lan_ip()
    print(f"  Generating self-signed TLS cert (LAN IP: {lan_ip}) …")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NMDisplay")])
    san_list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    if lan_ip != "127.0.0.1":
        san_list.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
        )
        .add_extension(
            x509.SubjectAlternativeName(san_list), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    print(f"  Cert saved to {cert_path}")
    return cert_path, key_path


app = Flask(__name__)


def _effective_my_node_id():
    """
    Return the reference node's integer ID.
    Prefers the runtime value written by NMListener on connect
    (listener_runtime.json); falls back to the manual override in settings.
    """
    runtime = listener_proc.get_runtime_info()
    if runtime.get("my_node_id") is not None:
        return runtime["my_node_id"]
    return cfg_mod.load().get("my_node_id")

# TILE_CACHE_DIR = "/mnt/nvme/tile_cache"
TILE_CACHE_DIR = "tile_cache"

# ── Stadia Maps API key ────────────────────────────────────────────────────────
# Required: server-side proxies do NOT get Stadia's localhost exemption.
# Free tier: 200k requests/month. Register at https://stadiamaps.com/
# Domain allowlist (no API key needed): set STADIA_REFERER to your hostname and
# register that hostname in the Stadia dashboard under "Allowed Domains".
# The proxy sends this as the Referer header so Stadia accepts the request.
# If you have an API key instead, set STADIA_API_KEY and leave STADIA_REFERER empty.
# REMOVE THIS BLOCK when removing all Stadia providers.
STADIA_REFERER   = "http://HAXXpi5.local/"   # your registered hostname, or "" to skip
STADIA_API_KEY   = ""                  # API key alternative; leave "" if using referer
# ── End Stadia block ───────────────────────────────────────────────────────────

# ── Thunderforest API key ──────────────────────────────────────────────────────
# Required for tf_atlas.
# REMOVE THIS BLOCK when removing all Thunderforest providers.
THUNDERFOREST_API_KEY = "ccd1da931ab04f45b3ca124f3f694f08"
# ── End Thunderforest block ────────────────────────────────────────────────────

# ── Google Maps API key ────────────────────────────────────────────────────────
# Used for reverse geocoding (lat/lon → street address) on the node info popup.
GOOGLE_MAPS_API_KEY = "AIzaSyBaB6pT0oSaZGqa_hTx5v6__gAVM259acc"
# ── End Google Maps block ──────────────────────────────────────────────────────

TILE_PROVIDERS = {
    # ── Original providers ─────────────────────────────────────────────────────
    "osm":  "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "hot":  "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    "topo": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",

    # ── Stadia Maps (STADIA_API_KEY appended automatically when set) ───────────
    # REMOVE stadia_smooth_dark entry to drop "Stadia Dark" layer.
    "stadia_smooth_dark": "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}.png",
    # ── End Stadia block ───────────────────────────────────────────────────────

    # ── Thunderforest (THUNDERFOREST_API_KEY appended automatically when set) ──
    # REMOVE tf_atlas entry to drop "TF Atlas" layer.
    "tf_atlas":     "https://api.thunderforest.com/atlas/{z}/{x}/{y}.png",
    # ── End Thunderforest block ────────────────────────────────────────────────
}

# # Providers whose upstream format is not PNG.
# # Maps provider key → (response_mimetype, cache_file_extension).
# # REMOVE an entry here when removing the corresponding provider above.
TILE_MIME = {
    "stadia_watercolor": ("image/jpeg", ".jpg"),
}

# Maps provider prefix → (query_param_name, api_key_value).
# The proxy appends ?param=key to the upstream URL when the key is non-empty.
# REMOVE an entry here when removing the corresponding provider group.
TILE_API_KEYS = {
    "stadia_":  ("api_key", STADIA_API_KEY),
    "tf_":      ("apikey",  THUNDERFOREST_API_KEY),
}

# Maps provider prefix → extra request headers sent to the upstream tile server.
# Stadia uses the Referer header for domain-allowlist auth (free tier, no API key).
# REMOVE an entry here when removing the corresponding provider group.
TILE_EXTRA_HEADERS = {
    "stadia_": {"Referer": STADIA_REFERER} if STADIA_REFERER else {},
}

OSM_HEADERS = {"User-Agent": "NMDisplay/1.0 NodeMonitor (amateur radio mesh monitor)"}


def _build_tile_url(provider: str, z: int, x: int, y: int) -> str:
    """Construct the upstream tile URL, appending any required API key."""
    url = TILE_PROVIDERS[provider].format(z=z, x=x, y=y)
    for prefix, (param, key) in TILE_API_KEYS.items():
        if provider.startswith(prefix) and key:
            sep = "&" if "?" in url else "?"
            url += f"{sep}{param}={key}"
            break
    return url


def _build_tile_headers(provider: str) -> dict:
    """Merge base headers with any provider-specific extra headers."""
    extra = {}
    for prefix, headers in TILE_EXTRA_HEADERS.items():
        if provider.startswith(prefix):
            extra = headers
            break
    return {**OSM_HEADERS, **extra}


# ------------------------------------------------------------------
# Main page
# ------------------------------------------------------------------

@app.route("/")
def index():
    cfg = cfg_mod.load()
    return render_template("index.html", cfg=cfg)


# ------------------------------------------------------------------
# Data API
# ------------------------------------------------------------------

@app.route("/api/nodes")
def api_nodes():
    my_node_id = _effective_my_node_id()
    conn = queries.get_conn()
    try:
        my_lat, my_lon = queries.get_my_node_position(conn, my_node_id)
        # Browser GPS overrides node GPS for distance calc if provided
        blat = request.args.get("blat", type=float)
        blon = request.args.get("blon", type=float)
        if blat is not None and blon is not None:
            my_lat, my_lon = blat, blon
        nodes = queries.get_all_nodes(conn, my_lat, my_lon)
    finally:
        conn.close()
    my_masked = my_node_id & 0xFFFFFFFF if my_node_id is not None else None
    for n in nodes:
        n["is_my_node"] = (n["node_id"] & 0xFFFFFFFF) == my_masked if my_masked is not None else False
    return jsonify({
        "nodes": nodes,
        "my_node_id": my_node_id,
        "server_time": int(time.time()),
    })


@app.route("/api/telemetry_lists")
def api_telemetry_lists():
    cfg = cfg_mod.load()
    my_node_id = _effective_my_node_id()
    cfg["my_node_id"] = my_node_id  # ensure telemetry list builder uses runtime value
    conn = queries.get_conn()
    try:
        my_lat, my_lon = queries.get_my_node_position(conn, my_node_id)
        blat = request.args.get("blat", type=float)
        blon = request.args.get("blon", type=float)
        if blat is not None and blon is not None:
            my_lat, my_lon = blat, blon
        nodes = queries.get_all_nodes(conn, my_lat, my_lon)
    finally:
        conn.close()
    lists = queries.build_telemetry_lists(nodes, cfg)
    return jsonify({"lists": lists})


@app.route("/api/timeline")
def api_timeline():
    cfg = cfg_mod.load()
    tl_cfg = cfg.get("timeline", {})
    # Support both old time_scale_hours and new time_scale_minutes
    if "time_scale_minutes" in tl_cfg:
        time_scale_minutes = int(tl_cfg["time_scale_minutes"])
    else:
        time_scale_minutes = int(tl_cfg.get("time_scale_hours", 6)) * 60
    time_scale_minutes = max(15, min(1440, time_scale_minutes))
    my_node_id = _effective_my_node_id()
    conn = queries.get_conn()
    try:
        buckets, session_minutes = queries.get_timeline(conn, time_scale_minutes, my_node_id)
    finally:
        conn.close()
    return jsonify({
        "buckets":          buckets,
        "time_scale_minutes": time_scale_minutes,
        "session_minutes":  session_minutes,
        "server_time":      int(time.time()),
    })


@app.route("/api/gauges")
def api_gauges():
    my_node_id = _effective_my_node_id()
    conn = queries.get_conn()
    try:
        my_lat, my_lon = queries.get_my_node_position(conn, my_node_id)
        blat = request.args.get("blat", type=float)
        blon = request.args.get("blon", type=float)
        if blat is not None and blon is not None:
            my_lat, my_lon = blat, blon
        data = queries.get_gauges(conn, my_node_id, my_lat, my_lon)
        data["hardware_breakdown"] = queries.get_hardware_breakdown(conn)
        data["role_breakdown"]     = queries.get_role_breakdown(conn)
    finally:
        conn.close()
    return jsonify(data)


@app.route("/api/nodes/<int:node_id>", methods=["DELETE"])
def api_delete_node(node_id):
    try:
        queries.delete_node(node_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/traffic")
def api_traffic():
    by    = request.args.get("by", "")
    value = request.args.get("value", "")
    if by not in ("portnum", "hardware", "role", "node"):
        return jsonify({"error": "invalid filter type", "packets": []}), 400
    conn = queries.get_conn()
    try:
        packets = queries.get_filtered_traffic(conn, by, value)
    finally:
        conn.close()
    return jsonify({"packets": packets, "count": len(packets)})


@app.route("/api/packet/<int:packet_id>")
def api_packet_detail(packet_id):
    conn = queries.get_conn()
    try:
        detail = queries.get_packet_detail(conn, packet_id)
    finally:
        conn.close()
    if detail is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(detail)


@app.route("/api/packet_breakdown")
def api_packet_breakdown():
    my_node_id = _effective_my_node_id()
    conn = queries.get_conn()
    try:
        rows = queries.get_packet_breakdown(conn, my_node_id)
    finally:
        conn.close()
    return jsonify({"breakdown": rows})


# ------------------------------------------------------------------
# Export API
# ------------------------------------------------------------------

_CSV_FIELDS = [
    "node_id_hex", "short_name", "long_name", "hardware_model", "role",
    "first_seen", "last_heard", "seeded",
    "lat", "lon", "altitude_m",
    "rssi", "snr", "battery_level", "voltage", "hop_limit",
    "total_packets", "sent_count", "received_count", "dead_end_count",
    "distance_km",
]


def _build_nodes_csv(conn) -> str:
    nodes = queries.get_all_nodes(conn, None, None)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(nodes)
    return buf.getvalue()


@app.route("/api/export/nodes.csv")
def api_export_nodes():
    """Export the active session's nodes as CSV."""
    conn = queries.get_conn()
    try:
        csv_data = _build_nodes_csv(conn)
    finally:
        conn.close()

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nodes_{timestamp}.csv"},
    )


_PACKET_CSV_FIELDS = [
    "id", "rx_time", "from_node_id", "to_node_id", "portnum",
    "hop_limit", "rx_rssi", "rx_snr", "rx_lat", "rx_lon",
]


def _build_packets_csv(conn) -> str:
    packets = queries.get_packets_for_export(conn)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_PACKET_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(packets)
    return buf.getvalue()


@app.route("/api/export/packets.csv")
def api_export_packets():
    """Export the active session's packets as CSV."""
    conn = queries.get_conn()
    try:
        csv_data = _build_packets_csv(conn)
    finally:
        conn.close()
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=packets_{timestamp}.csv"},
    )


@app.route("/api/sessions/<session_id>/export/packets.csv")
def api_session_export_packets(session_id):
    """Export a specific session's packets as CSV."""
    import bundle_paths as bp
    all_sessions = sess_mod.load_all()
    session = next((s for s in all_sessions if s["id"] == session_id), None)
    if session is None:
        abort(404)
    db_path = os.path.join(bp.data_dir(), session["db"])
    if not os.path.exists(db_path):
        abort(404)

    conn = queries.get_conn_for_path(db_path)
    try:
        csv_data = _build_packets_csv(conn)
    finally:
        conn.close()

    slug = re.sub(r"[^a-z0-9]+", "_", session["name"].lower()).strip("_")
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"packets_{slug}_{timestamp}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/sessions/exportable")
def api_sessions_exportable():
    """Return all sessions with an indicator of whether their DB file exists."""
    import bundle_paths as bp
    all_sessions = sess_mod.load_all()
    result = []
    for s in all_sessions:
        db_path = os.path.join(bp.data_dir(), s["db"])
        result.append({
            "id": s["id"],
            "name": s["name"],
            "created": s.get("created"),
            "db_exists": os.path.exists(db_path),
        })
    return jsonify({"sessions": result})


@app.route("/api/sessions/<session_id>/export/nodes.csv")
def api_session_export_nodes(session_id):
    """Export a specific session's nodes as CSV."""
    import bundle_paths as bp
    all_sessions = sess_mod.load_all()
    session = next((s for s in all_sessions if s["id"] == session_id), None)
    if session is None:
        abort(404)
    db_path = os.path.join(bp.data_dir(), session["db"])
    if not os.path.exists(db_path):
        abort(404)

    conn = queries.get_conn_for_path(db_path)
    try:
        csv_data = _build_nodes_csv(conn)
    finally:
        conn.close()

    slug = re.sub(r"[^a-z0-9]+", "_", session["name"].lower()).strip("_")
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"nodes_{slug}_{timestamp}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ------------------------------------------------------------------
# Settings API
# ------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(cfg_mod.load())


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    patch = request.get_json(force=True, silent=True) or {}
    current = cfg_mod.load()
    merged, errors = cfg_mod.validate_and_merge(current, patch)
    if errors:
        return jsonify({"errors": errors}), 400
    cfg_mod.save(merged)
    return jsonify({"updated": merged})


# ------------------------------------------------------------------
# Sessions API
# ------------------------------------------------------------------

@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    all_sessions = sess_mod.load_all()
    active = sess_mod.get_active()
    return jsonify({"sessions": all_sessions, "active_id": active["id"]})


@app.route("/api/sessions/new", methods=["POST"])
def api_sessions_new():
    # Cannot create a new session while the listener is running — the new
    # session's connection config won't take effect until the listener restarts.
    st = listener_proc.status()
    if st["state"] == "running":
        return jsonify({"error": "Stop the listener before creating a new session."}), 409

    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    seed = bool(data.get("seed_on_connect", True))
    save = bool(data.get("save_packet_data", True))
    conn_cfg = data.get("connection") or {}
    if conn_cfg.get("type") not in ("serial", "tcp", "ble"):
        conn_cfg = {"type": "serial"}
    session = sess_mod.create(name, conn_cfg, seed_on_connect=seed, save_packet_data=save)
    return jsonify({"session": session})


@app.route("/api/sessions/<session_id>/activate", methods=["POST"])
def api_sessions_activate(session_id):
    # Cannot switch sessions while listener is running
    st = listener_proc.status()
    if st["state"] == "running":
        return jsonify({"error": "Stop the listener before switching sessions."}), 409
    try:
        session = sess_mod.activate(session_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"session": session})


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_sessions_delete(session_id):
    try:
        sess_mod.delete(session_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Listener control API
# ------------------------------------------------------------------

@app.route("/api/listener/status", methods=["GET"])
def api_listener_status():
    st = listener_proc.status()
    active = sess_mod.get_active()
    st["session_name"] = active.get("name", "—")
    st["connection"] = active.get("connection", {})
    st["save_packet_data"] = active.get("save_packet_data", True)
    # Merge in runtime info (node name, channel, frequency) written by NMListener on connect
    st.update(listener_proc.get_runtime_info())
    return jsonify(st)


@app.route("/api/listener/start", methods=["POST"])
def api_listener_start():
    return jsonify(listener_proc.start())


@app.route("/api/listener/stop", methods=["POST"])
def api_listener_stop():
    return jsonify(listener_proc.stop())


@app.route("/api/listener/log", methods=["GET"])
def api_listener_log():
    n = min(int(request.args.get("n", 80)), 200)
    return jsonify({"lines": listener_proc.get_log(n)})


@app.route("/api/listener/requeue-nodes", methods=["POST"])
def api_listener_requeue_nodes():
    """Write the requeue trigger file; NMListener picks it up within 5 s."""
    import bundle_paths as bp
    trigger = os.path.join(bp.data_dir(), ".requeue_trigger")
    try:
        open(trigger, "w").close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/active/save_packet_data", methods=["PATCH"])
def api_patch_save_packet_data():
    """Toggle save_packet_data for the active session immediately."""
    data = request.get_json(force=True, silent=True) or {}
    value = data.get("save_packet_data")
    if not isinstance(value, bool):
        return jsonify({"error": "save_packet_data must be a boolean"}), 400
    sess_mod.patch_active_flag("save_packet_data", value)
    return jsonify({"ok": True, "save_packet_data": value})


@app.route("/api/listener/scan/serial")
def api_scan_serial():
    """Return a list of available serial ports (USB-connected nodes)."""
    ports = []
    try:
        import serial.tools.list_ports  # type: ignore
        for p in serial.tools.list_ports.comports():
            ports.append({"device": p.device, "description": p.description or ""})
    except Exception as e:
        return jsonify({"error": str(e), "ports": []}), 200
    return jsonify({"ports": ports})


@app.route("/api/listener/scan/ble")
def api_scan_ble():
    """Scan for nearby Meshtastic BLE devices. Delegates to NMListener's Python
    (which has meshtastic+bleak installed) via subprocess to avoid CoreBluetooth
    thread restrictions inside the Flask process."""
    import json as _json
    import subprocess
    import sys

    ld = bundle_paths.listener_source_dir()
    venv_py = os.path.join(ld, ".venv", "bin", "python3")
    python = venv_py if os.path.exists(venv_py) else sys.executable

    scan_code = (
        "import json, sys\n"
        "try:\n"
        "    from meshtastic.ble_interface import BLEInterface\n"
        "    devices = BLEInterface.scan()\n"
        "    print(json.dumps([{'name': d.name or '', 'address': d.address}"
        " for d in devices]))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )

    try:
        proc = subprocess.run(
            [python, "-c", scan_code],
            capture_output=True, text=True, timeout=15,
            cwd=ld,
        )
        raw = proc.stdout.strip()
        if not raw:
            err = proc.stderr.strip() or "No output from scan"
            return jsonify({"error": err, "devices": []}), 200
        data = _json.loads(raw)
        if isinstance(data, dict) and "error" in data:
            return jsonify({"error": data["error"], "devices": []}), 200
        return jsonify({"devices": data})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Scan timed out after 15s", "devices": []}), 200
    except Exception as e:
        return jsonify({"error": str(e), "devices": []}), 200


# ------------------------------------------------------------------
# Tile proxy
# ------------------------------------------------------------------

@app.route("/tiles/<provider>/<int:z>/<int:x>/<int:y>.png")
def tile(provider, z, x, y):
    if provider not in TILE_PROVIDERS:
        abort(404)
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(400)

    mime_type, cache_ext = TILE_MIME.get(provider, ("image/png", ".png"))
    path = os.path.join(TILE_CACHE_DIR, provider, str(z), str(x), f"{y}{cache_ext}")

    if not os.path.exists(path):
        url = _build_tile_url(provider, z, x, y)
        last_exc = None
        for attempt in range(3):
            try:
                resp = req_lib.get(url, headers=_build_tile_headers(provider), timeout=10)
                resp.raise_for_status()
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    import time; time.sleep(0.5 * (attempt + 1))
        if last_exc is not None:
            abort(502)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(resp.content)

    return send_file(path, mimetype=mime_type)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run_server() -> None:
    """Start the Flask server. Blocks until the server exits."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    os.makedirs(TILE_CACHE_DIR, exist_ok=True)
    sess_mod.get_active()
    geocode_mod.start(GOOGLE_MAPS_API_KEY)
    cert, key = _ensure_ssl_cert()
    lan_ip = _get_lan_ip()
    print("\nNMDisplay — Meshtastic Node Monitor")
    print(f"  Local:  https://localhost:5000")
    print(f"  iPad:   https://{lan_ip}:5000")
    print("  (Accept the self-signed cert warning on first visit)\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True,
            ssl_context=(cert, key))


if __name__ == "__main__":
    run_server()
