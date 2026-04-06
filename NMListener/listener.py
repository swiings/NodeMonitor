"""
listener.py — pubsub subscriptions and packet dispatch.

Call register() once after each new TCPInterface is created.
Call unregister() before closing the interface and reconnecting.
"""

import logging
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

# Signals main loop when the connection drops
connection_lost_event = threading.Event()


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
    log.info("Connected to Meshtastic node")
    connection_lost_event.clear()
    if _repo is not None and _seed_on_connect:
        seeder.seed_nodes(interface, _repo, _node_store)
    elif _repo is not None:
        log.info("Seeding disabled for this session — relying on live packets")
    if _repo is not None:
        node_requester.start(interface, _repo, _node_store, seed_on_connect=_seed_on_connect)


def _on_lost(interface, topic=pub.AUTO_TOPIC) -> None:
    log.warning("Connection to Meshtastic node lost")
    connection_lost_event.set()
    node_requester.stop()


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
    rssi_str = f"rssi={rssi}" if rssi is not None else ""
    snr_str = f"snr={snr}" if snr is not None else ""
    sig = "  ".join(filter(None, [rssi_str, snr_str]))

    print(f"[{ts}] RECV {portnum:<22} from={from_str}  {sig}{extras}")
