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
import socket
import time

import requests as req_lib
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

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
    cert_path = os.path.join(_here, "cert.pem")
    key_path  = os.path.join(_here, "key.pem")
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

_here = os.path.dirname(os.path.abspath(__file__))
TILE_CACHE_DIR = os.path.join(_here, "tile_cache")

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
    cfg = cfg_mod.load()
    my_node_id = cfg.get("my_node_id")
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
    my_node_id = cfg.get("my_node_id")
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
    time_scale = cfg.get("timeline", {}).get("time_scale_hours", 6)
    my_node_id = cfg.get("my_node_id")
    conn = queries.get_conn()
    try:
        buckets = queries.get_timeline(conn, time_scale, my_node_id)
    finally:
        conn.close()
    return jsonify({
        "buckets": buckets,
        "time_scale_hours": time_scale,
        "server_time": int(time.time()),
    })


@app.route("/api/gauges")
def api_gauges():
    cfg = cfg_mod.load()
    my_node_id = cfg.get("my_node_id")
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


# ------------------------------------------------------------------
# Export API
# ------------------------------------------------------------------

@app.route("/api/export/nodes.csv")
def api_export_nodes():
    cfg = cfg_mod.load()
    my_node_id = cfg.get("my_node_id")
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

    fields = [
        "node_id_hex", "short_name", "long_name", "hardware_model", "role",
        "first_seen", "last_heard", "seeded",
        "lat", "lon", "altitude_m",
        "rssi", "snr", "battery_level", "voltage", "hop_limit",
        "total_packets", "sent_count", "received_count", "dead_end_count",
        "distance_km",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(nodes)

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"nodes_{timestamp}.csv"

    return Response(
        buf.getvalue(),
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
    session = sess_mod.create(name, conn_cfg, seed_on_connect=seed)
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

if __name__ == "__main__":
    import logging
    # Suppress per-request GET lines from werkzeug — only show errors
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
