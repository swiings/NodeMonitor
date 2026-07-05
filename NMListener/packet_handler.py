"""
packet_handler.py — Categorizes and persists incoming Meshtastic packets.

dispatch() is the single entry point called for every received packet.
It always calls handle_generic() (timeline + node upsert), then routes to
the portnum-specific handler.
"""

import json
import os
import time
from typing import Optional

import config
from db.repository import Repository
from store.counters import MessageCounter
from store.node_store import NodeStore
from store.timeline import PacketTimeline

# Reference node ID — packets from this node are excluded from DB writes
# and timeline counts. Set via set_my_node() when the listener connects.
_my_node_id: Optional[int] = None

# Latest reference-node telemetry is cached here for NMDisplay to read
# (title-row sentence). Mirrors listener.py's NMData path resolution.
_NM_DATA_DIR = os.environ.get("NM_DATA_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "NMData")
)
_REFERENCE_TELEMETRY_FILE = os.path.join(_NM_DATA_DIR, "reference_telemetry.json")


def set_my_node(node_id: Optional[int]) -> None:
    """Called by listener on connect with the reference node's integer ID."""
    global _my_node_id
    _my_node_id = node_id


def get_rx_position(interface) -> tuple[Optional[float], Optional[float]]:
    """Return our own node's current GPS position as (lat, lon) float degrees, or (None, None)."""
    try:
        my_num = interface.myInfo.my_node_num
        node = interface.nodesByNum.get(my_num)
        if node and "position" in node:
            pos = node["position"]
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            if lat is not None and lon is not None:
                return float(lat), float(lon)
    except (AttributeError, KeyError, TypeError):
        pass
    return None, None


def dispatch(
    packet: dict,
    interface,
    repo: Repository,
    node_store: NodeStore,
    timeline: PacketTimeline,
    msg_counter: MessageCounter,
) -> None:
    rx_lat, rx_lon = get_rx_position(interface)
    handle_generic(packet, interface, repo, node_store, timeline, rx_lat, rx_lon)

    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum", "UNKNOWN")

    if portnum == "TEXT_MESSAGE_APP":
        handle_text_message(packet, repo, node_store, msg_counter, rx_lat, rx_lon)
    elif portnum == "TELEMETRY_APP":
        handle_telemetry(packet, repo, node_store, rx_lat, rx_lon)
    elif portnum == "NODEINFO_APP":
        handle_nodeinfo(packet, repo, node_store, rx_lat, rx_lon)
    elif portnum == "POSITION_APP":
        handle_position(packet, repo, node_store, rx_lat, rx_lon)
    # Unknown portnums are already counted by handle_generic


def handle_generic(
    packet: dict,
    interface,
    repo: Repository,
    node_store: NodeStore,
    timeline: PacketTimeline,
    rx_lat: Optional[float],
    rx_lon: Optional[float],
) -> None:
    """Always runs for every packet: insert into packets table, update timeline."""
    from_id = packet.get("from") or packet.get("fromId")
    to_id = packet.get("to") or packet.get("toId")
    rx_time = packet.get("rxTime") or int(time.time())
    rssi = packet.get("rxRssi")
    snr = packet.get("rxSnr")
    hop_limit = packet.get("hopLimit")
    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum", "UNKNOWN")
    portnum_int = _portnum_int(decoded)

    if from_id is None:
        return

    from_id = _to_int(from_id)
    to_id = _to_int(to_id) if to_id is not None else None

    # Skip packets originating from the reference node itself
    if _my_node_id is not None and from_id == _my_node_id:
        return

    # Refine encrypted (UNKNOWN) packets into broadcast vs. unicast subcategories
    if portnum == "UNKNOWN":
        if to_id is not None and (to_id & 0xFFFFFFFF) == 0xFFFFFFFF:
            portnum = "UNKNOWN_PRIVATE_CH"
        elif to_id is not None:
            portnum = "UNKNOWN_DIRECT"

    # Always update in-memory node store and the live DB view (nodes table,
    # timeline buckets) so gauges and the map stay current regardless of the
    # save-packet-data flag.
    node_store.upsert(from_id, rssi=rssi, snr=snr, hop_limit=hop_limit)
    repo.upsert_node(from_id, rssi=rssi, snr=snr, hop_limit=hop_limit, last_heard=rx_time)

    # Increment per-minute timeline (in-memory + DB aggregate)
    minute_ts = timeline.record(portnum, rx_time)
    repo.increment_timeline(minute_ts, portnum)

    # Insert raw packet row only when saving is enabled
    if config.SAVE_PACKET_DATA:
        repo.insert_packet(
            rx_time=rx_time,
            from_node_id=from_id,
            to_node_id=to_id,
            portnum=portnum,
            portnum_int=portnum_int,
            hop_limit=hop_limit,
            rx_rssi=rssi,
            rx_snr=snr,
            rx_lat=rx_lat,
            rx_lon=rx_lon,
            payload_raw=None,
            payload_dict=decoded if decoded else None,
        )


def handle_text_message(
    packet: dict,
    repo: Repository,
    node_store: NodeStore,
    msg_counter: MessageCounter,
    rx_lat: Optional[float],
    rx_lon: Optional[float],
) -> None:
    decoded = packet.get("decoded", {})
    text = decoded.get("text") or decoded.get("payload", b"").decode("utf-8", errors="replace")
    from_id = _to_int(packet.get("from") or packet.get("fromId"))
    to_id_raw = packet.get("to") or packet.get("toId")
    to_id = _to_int(to_id_raw) if to_id_raw is not None else None
    rx_time = packet.get("rxTime") or int(time.time())

    if not text or from_id is None:
        return

    # Get the most recently inserted packet id for this node/time
    packet_id = _last_packet_id(repo)
    if packet_id is None:
        return

    repo.insert_text_message(
        packet_id,
        rx_time=rx_time,
        from_node_id=from_id,
        to_node_id=to_id,
        message_text=text,
    )

    # Count: sender sent, all others received
    msg_counter.record_sent(from_id)
    repo.upsert_message_count(from_id, sent_delta=1)

    if to_id and to_id != 0xFFFFFFFF:
        msg_counter.record_received(to_id)
        repo.upsert_message_count(to_id, received_delta=1)


def handle_telemetry(
    packet: dict,
    repo: Repository,
    node_store: NodeStore,
    rx_lat: Optional[float],
    rx_lon: Optional[float],
) -> None:
    decoded = packet.get("decoded", {})
    telemetry = decoded.get("telemetry", {})
    from_id = _to_int(packet.get("from") or packet.get("fromId"))
    rx_time = packet.get("rxTime") or int(time.time())

    if from_id is None:
        return

    device = telemetry.get("deviceMetrics", {})
    env = telemetry.get("environmentMetrics", {})

    battery = device.get("batteryLevel")
    voltage = device.get("voltage")
    chan_util = device.get("channelUtilization")
    air_util = device.get("airUtilTx")

    temperature = env.get("temperature")
    humidity = env.get("relativeHumidity")
    pressure = env.get("barometricPressure")
    gas_resistance = env.get("iaq") or env.get("gasResistance") or env.get("gas_resistance") or env.get("gas")

    # for debugging: show the entire telemetry dict, not just keys
    # if env:
    #     print(f"DEBUG FULL ENV: {env}")  # Show entire dict, not just keys

    # LocalStats variant carries the node's own receiver noise floor (dBm)
    local_stats = telemetry.get("localStats", {})
    noise_floor = local_stats.get("noiseFloor")

    # Keep the node's battery/voltage current regardless of origin.
    node_store.upsert(from_id, battery_level=battery, voltage=voltage)
    repo.upsert_node(from_id, battery_level=battery, voltage=voltage, last_heard=rx_time)

    # Reference-node telemetry (arrives over the local serial/USB/BLE link) is
    # cached to a JSON file for NMDisplay's title-row sentence rather than the
    # packet DB — its self-echo has no packet row and no RF signal metrics.
    if _my_node_id is not None and from_id == _my_node_id:
        _write_reference_telemetry({
            "battery_level":       battery,
            "voltage":             voltage,
            "temperature":         temperature,
            "relative_humidity":   humidity,
            "barometric_pressure": pressure,
            "gas_resistance":      gas_resistance,
            "noise_floor":         noise_floor,
            "rx_time":             rx_time,
        })
        return

    packet_id = _last_packet_id(repo)
    if packet_id is None:
        return

    repo.insert_telemetry(
        packet_id,
        rx_time=rx_time,
        node_id=from_id,
        battery_level=battery,
        voltage=voltage,
        channel_util=chan_util,
        air_util_tx=air_util,
        temperature=temperature,
        relative_humidity=humidity,
        barometric_pressure=pressure,
        gas_resistance=gas_resistance,
    )


def handle_nodeinfo(
    packet: dict,
    repo: Repository,
    node_store: NodeStore,
    rx_lat: Optional[float],
    rx_lon: Optional[float],
) -> None:
    decoded = packet.get("decoded", {})
    user = decoded.get("user", {})
    from_id = _to_int(packet.get("from") or packet.get("fromId"))
    rx_time = packet.get("rxTime") or int(time.time())

    if from_id is None:
        return

    short_name = user.get("shortName")
    long_name = user.get("longName")
    hw_model = user.get("hwModel")
    # protobuf3 omits default-value fields; CLIENT = 0 is the default role
    role = user.get("role")
    role = str(role) if role is not None else "CLIENT"

    node_store.upsert(from_id, short_name=short_name, long_name=long_name,
                      hardware_model=hw_model, role=role)
    repo.upsert_node(
        from_id,
        short_name=short_name,
        long_name=long_name,
        hardware_model=str(hw_model) if hw_model is not None else None,
        role=role,
        last_heard=rx_time,
    )


def handle_position(
    packet: dict,
    repo: Repository,
    node_store: NodeStore,
    rx_lat: Optional[float],
    rx_lon: Optional[float],
) -> None:
    decoded = packet.get("decoded", {})
    pos = decoded.get("position", {})
    from_id = _to_int(packet.get("from") or packet.get("fromId"))
    rx_time = packet.get("rxTime") or int(time.time())

    if from_id is None:
        return

    lat = pos.get("latitude")
    lon = pos.get("longitude")
    alt = pos.get("altitude")
    precision = pos.get("precisionBits")

    if lat is None or lon is None:
        return

    lat = float(lat)
    lon = float(lon)

    node_store.upsert(from_id, lat=lat, lon=lon, altitude_m=alt)
    repo.upsert_node(from_id, lat=lat, lon=lon, altitude_m=alt, last_heard=rx_time)

    packet_id = _last_packet_id(repo)
    if packet_id is None:
        return

    repo.insert_position(
        packet_id,
        rx_time=rx_time,
        node_id=from_id,
        lat=lat,
        lon=lon,
        altitude_m=float(alt) if alt is not None else None,
        precision_bits=precision,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_reference_telemetry(metrics: dict) -> None:
    """
    Merge the given metrics into reference_telemetry.json. Device-metric and
    environment-metric packets arrive separately, so we merge (only non-None
    values overwrite) to keep a complete picture for the title-row sentence.
    """
    try:
        existing: dict = {}
        if os.path.exists(_REFERENCE_TELEMETRY_FILE):
            with open(_REFERENCE_TELEMETRY_FILE) as f:
                existing = json.load(f)
    except Exception:
        existing = {}

    for key, value in metrics.items():
        if value is not None:
            existing[key] = value

    try:
        with open(_REFERENCE_TELEMETRY_FILE, "w") as f:
            json.dump(existing, f)
    except Exception:
        pass


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # Meshtastic node IDs often appear as "!aabbccdd" (hex) or decimal
        s = value.strip()
        if s.startswith("!"):
            try:
                return int(s[1:], 16)
            except ValueError:
                pass
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _portnum_int(decoded: dict) -> Optional[int]:
    """Extract raw portnum integer if available (for unknown portnums)."""
    raw = decoded.get("portnumInt") or decoded.get("portnum_int")
    if isinstance(raw, int):
        return raw
    return None


def _last_packet_id(repo: Repository) -> Optional[int]:
    """Return the rowid of the most recently inserted packet."""
    cur = repo._conn.execute("SELECT MAX(id) FROM packets")
    row = cur.fetchone()
    return row[0] if row else None
