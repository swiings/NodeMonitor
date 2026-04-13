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

TILE_CACHE_DIR = os.path.join(bundle_paths.display_dir(), "tile_cache")

TILE_PROVIDERS = {
    "osm":  "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "hot":  "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    "topo": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
}
OSM_HEADERS = {"User-Agent": "NMDisplay/1.0 NodeMonitor (amateur radio mesh monitor)"}


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
    finally:
        conn.close()
    return jsonify(data)


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
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    conn_cfg = data.get("connection")
    if not conn_cfg or conn_cfg.get("type") not in ("tcp", "serial", "ble"):
        return jsonify({"error": "connection.type must be tcp, serial, or ble"}), 400

    # Validate TCP-specific fields
    if conn_cfg["type"] == "tcp":
        if not conn_cfg.get("host"):
            return jsonify({"error": "connection.host is required for TCP"}), 400
        conn_cfg["port"] = int(conn_cfg.get("port") or 4403)

    seed = bool(data.get("seed_on_connect", True))
    save = bool(data.get("save_packet_data", True))
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
        import serial.tools.list_ports
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
    if not (0 <= z <= 18 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(400)

    path = os.path.join(TILE_CACHE_DIR, provider, str(z), str(x), f"{y}.png")

    if not os.path.exists(path):
        url = TILE_PROVIDERS[provider].format(z=z, x=x, y=y)
        try:
            resp = req_lib.get(url, headers=OSM_HEADERS, timeout=10)
            resp.raise_for_status()
        except Exception:
            abort(502)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(resp.content)

    return send_file(path, mimetype="image/png")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run_server() -> None:
    """Start the Flask server. Blocks until the server exits."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    os.makedirs(TILE_CACHE_DIR, exist_ok=True)
    sess_mod.get_active()
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
