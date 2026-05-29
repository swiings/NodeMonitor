"""
listener.py — pubsub subscriptions and packet dispatch.

Call register() once after each new TCPInterface is created.
Call unregister() before closing the interface and reconnecting.
"""

import json
import logging
import os
import threading
import time

from pubsub import pub

import config
import node_requester
import packet_handler
import seeder
from db.repository import Repository
from store.counters import MessageCounter
from store.node_store import NodeStore
from store.timeline import PacketTimeline

log = logging.getLogger(__name__)

# Shared state, injected via register()
_repo: Repository | None = None
_node_store: NodeStore | None = None
_timeline: PacketTimeline | None = None
_msg_counter: MessageCounter | None = None
_seed_on_connect: bool = True
_interface = None  # held so the poll thread can act on trigger files

# Signals main loop when the connection drops
connection_lost_event = threading.Event()

# NMData path — mirrors connection.py logic
_here = os.path.dirname(os.path.abspath(__file__))
_nm_data_dir = os.environ.get("NM_DATA_DIR") or os.path.abspath(
    os.path.join(_here, "..", "NMData")
)
_RUNTIME_FILE            = os.path.join(_nm_data_dir, "listener_runtime.json")
_REQUEUE_TRIGGER         = os.path.join(_nm_data_dir, ".requeue_trigger")
_REQUEST_POSITION_TRIGGER = os.path.join(_nm_data_dir, ".request_position_trigger")

# Background polling thread state
_poll_thread: threading.Thread | None = None
_poll_stop = threading.Event()


# ------------------------------------------------------------------
# pubsub callbacks
# ------------------------------------------------------------------

def _on_receive(packet: dict, interface) -> None:
    _log_packet(packet)
    if _repo is None:
        return
    try:
        packet_handler.dispatch(
            packet, interface, _repo, _node_store, _timeline, _msg_counter
        )
    except Exception:
        log.exception("Error handling packet")

    # Queue a nodeinfo request if this sender has no name yet
    if _node_store is not None:
        from_raw = packet.get("from") or packet.get("fromId")
        from_int = None
        if isinstance(from_raw, int):
            from_int = from_raw
        elif isinstance(from_raw, str) and from_raw.startswith("!"):
            try:
                from_int = int(from_raw[1:], 16)
            except ValueError:
                pass
        if from_int is not None:
            rec = _node_store.get(from_int)
            if rec is not None and rec.short_name is None:
                node_requester.add(f"!{from_int & 0xFFFFFFFF:08x}")


def _on_connected(interface, topic=pub.AUTO_TOPIC) -> None:
    global _interface
    log.info("Connected to Meshtastic node")
    connection_lost_event.clear()
    _interface = interface

    # Capture reference node identity and write runtime file
    my_node_id = _read_my_node_id(interface)
    _write_runtime_info(interface, my_node_id)
    packet_handler.set_my_node(my_node_id)
    log.info("Reference node ID: %s", f"!{my_node_id:08x}" if my_node_id else "unknown")

    if _repo is not None and _seed_on_connect:
        seeder.seed_nodes(interface, _repo, _node_store)
    elif _repo is not None:
        log.info("Seeding disabled for this session — relying on live packets")
    if _repo is not None:
        node_requester.start(interface, _repo, _node_store, seed_on_connect=_seed_on_connect)

    _request_reference_position(interface, my_node_id)
    _start_poll_thread()


def _request_reference_position(interface, my_node_id: int | None) -> None:
    """Trigger an immediate position broadcast from the reference node on connect."""
    try:
        local_node = (interface.nodes or {}).get(
            f"!{my_node_id:08x}" if my_node_id else "", {}
        )
        pos = local_node.get("position", {})
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            interface.sendPosition(
                latitude=lat,
                longitude=lon,
                altitude=int(pos.get("altitude", 0)),
            )
            log.info("Triggered position broadcast from reference node (lat=%.5f lon=%.5f)", lat, lon)
        else:
            log.info("Reference node has no stored GPS fix — position broadcast skipped")
    except Exception as exc:
        log.warning("Could not trigger position broadcast: %s", exc)


def _on_lost(interface, topic=pub.AUTO_TOPIC) -> None:
    global _interface
    log.warning("Connection to Meshtastic node lost")
    connection_lost_event.set()
    _interface = None
    node_requester.stop()
    _stop_poll_thread()


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

def register(
    interface,
    repo: Repository,
    node_store: NodeStore,
    timeline: PacketTimeline,
    msg_counter: MessageCounter,
    seed_on_connect: bool = True,
) -> None:
    global _repo, _node_store, _timeline, _msg_counter, _seed_on_connect
    _repo = repo
    _node_store = node_store
    _timeline = timeline
    _msg_counter = msg_counter
    _seed_on_connect = seed_on_connect
    connection_lost_event.clear()

    pub.subscribe(_on_receive, "meshtastic.receive")
    pub.subscribe(_on_connected, "meshtastic.connection.established")
    pub.subscribe(_on_lost, "meshtastic.connection.lost")


def unregister() -> None:
    global _repo, _node_store, _timeline, _msg_counter, _seed_on_connect
    _stop_poll_thread()
    node_requester.stop()
    for fn, topic in [
        (_on_receive, "meshtastic.receive"),
        (_on_connected, "meshtastic.connection.established"),
        (_on_lost, "meshtastic.connection.lost"),
    ]:
        try:
            pub.unsubscribe(fn, topic)
        except Exception:
            pass
    _repo = None
    _node_store = None
    _timeline = None
    _msg_counter = None
    _seed_on_connect = True


# ------------------------------------------------------------------
# Background polling (config reload + requeue trigger)
# ------------------------------------------------------------------

def _start_poll_thread() -> None:
    global _poll_thread
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="nm-poll")
    _poll_thread.start()


def _stop_poll_thread() -> None:
    _poll_stop.set()


def _poll_loop() -> None:
    """Every 5 s: reload active.json flags and check for requeue trigger."""
    while not _poll_stop.wait(5):
        # Reload save_packet_data flag
        try:
            config.reload()
        except Exception:
            pass

        # Requeue trigger
        if os.path.exists(_REQUEUE_TRIGGER):
            try:
                os.remove(_REQUEUE_TRIGGER)
                if _repo is not None:
                    node_requester.requeue_unresolved(_repo)
                    log.info("Re-queued unresolved nodes for name lookup")
            except Exception:
                log.exception("Error processing requeue trigger")

        # Manual position request trigger
        if os.path.exists(_REQUEST_POSITION_TRIGGER):
            try:
                os.remove(_REQUEST_POSITION_TRIGGER)
                if _interface is not None:
                    _request_reference_position(_interface, _read_my_node_id(_interface))
                    log.info("Manual position request processed")
                else:
                    log.warning("Position request trigger found but no interface connected")
            except Exception:
                log.exception("Error processing position request trigger")


# ------------------------------------------------------------------
# Runtime info helpers
# ------------------------------------------------------------------

def _read_my_node_id(interface) -> int | None:
    try:
        return interface.myInfo.my_node_num
    except (AttributeError, TypeError):
        return None


def _write_runtime_info(interface, my_node_id: int | None) -> None:
    info: dict = {}
    if my_node_id is not None:
        info["my_node_id"] = my_node_id

        # Short name from node cache
        try:
            my_hex = f"!{my_node_id:08x}"
            node_data = (interface.nodes or {}).get(my_hex, {})
            info["my_node_short"] = node_data.get("user", {}).get("shortName") or ""
        except Exception:
            info["my_node_short"] = ""

        # LoRa channel + frequency
        try:
            lora = interface.localNode.localConfig.lora
            info["channel_num"] = int(lora.channel_num)
            freq = _compute_frequency(lora)
            if freq is not None:
                info["frequency_mhz"] = freq
        except Exception:
            pass

    try:
        with open(_RUNTIME_FILE, "w") as f:
            json.dump(info, f)
        log.debug("Wrote %s", _RUNTIME_FILE)
    except Exception:
        log.warning("Could not write listener_runtime.json")


def _compute_frequency(lora) -> float | None:
    """Compute centre frequency in MHz from LoRa config."""
    # Try meshtastic's own utility first (available in some library versions)
    try:
        from meshtastic import util as mu
        return round(mu.compute_freq(lora.channel_num, lora.modem_preset), 3)
    except Exception:
        pass
    # Fallback: Meshtastic US915 formula. channel_num is 1-indexed (matches
    # Heltec display); subtract 1 to get the 0-based slot offset.
    try:
        return round(902.0 + (int(lora.channel_num) - 1) * 0.25 + 0.125, 3)
    except Exception:
        return None


# ------------------------------------------------------------------
# Console logging
# ------------------------------------------------------------------

def _log_packet(packet: dict) -> None:
    if not config.PACKET_LOG_VERBOSE:
        return

    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum", "UNKNOWN")
    from_id = packet.get("from") or packet.get("fromId", "?")
    rssi = packet.get("rxRssi")
    snr = packet.get("rxSnr")
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(packet.get("rxTime") or time.time()))

    extras = ""
    if portnum == "POSITION_APP":
        pos = decoded.get("position", {})
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            extras = f"  lat={lat:.5f}  lon={lon:.5f}"
    elif portnum == "NODEINFO_APP":
        user = decoded.get("user", {})
        name = user.get("shortName") or user.get("longName", "")
        if name:
            extras = f'  name="{name}"'
    elif portnum == "TELEMETRY_APP":
        dm = decoded.get("telemetry", {}).get("deviceMetrics", {})
        batt = dm.get("batteryLevel")
        if batt is not None:
            extras = f"  batt={batt}%"
    elif portnum == "TEXT_MESSAGE_APP":
        text = decoded.get("text", "")
        if text:
            extras = f'  msg="{text[:40]}"'

    from_str = f"!{from_id:08x}" if isinstance(from_id, int) else str(from_id)

    name_str = ""
    if _node_store is not None and isinstance(from_id, int):
        rec = _node_store.get(from_id)
        if rec is not None:
            parts = list(dict.fromkeys(p for p in [rec.short_name, rec.long_name] if p))
            if parts:
                name_str = f"  ({' / '.join(parts)})"

    rssi_str = f"rssi={rssi}" if rssi is not None else ""
    snr_str = f"snr={snr}" if snr is not None else ""
    sig = "  ".join(filter(None, [rssi_str, snr_str]))

    print(f"[{ts}] RECV {portnum:<22} from={from_str}{name_str}  {sig}{extras}")
