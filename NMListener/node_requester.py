"""
node_requester.py — Background queue that actively requests node info
for nodes heard on the mesh that have no short_name yet.

State machine per node (persisted in the requestor_queue DB table):
  PENDING  → SENT    (request transmitted with wantAck=True)
  SENT     → ACKED   (routing ACK received via meshtastic.routing pubsub)
  SENT     → NACKED  (routing NACK or timeout received)
  NACKED   → SENT    (retry after NACK_COOLDOWN seconds, up to MAX_ATTEMPTS)
  any      → deleted  (NODEINFO packet with names arrives for that node)

One request fires every INTERVAL_S seconds to avoid flooding the channel.
The routing callbacks in listener.py call ack() / nack() to advance state.
"""

import logging
import threading

log = logging.getLogger(__name__)

INTERVAL_S    = 30   # seconds between outbound requests
NACK_COOLDOWN = 120  # seconds before retrying a NACKED node
MAX_ATTEMPTS  = 3

_lock       = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_iface = None
_repo  = None

# In-session dedup set — fast cache to avoid a DB write on every packet
# from an unknown node.  The DB queue is the authoritative state.
# Cleared on each reconnect via start().
_seen: set = set()

_SKIP_IDS = {"!ffffffff", "!00000000"}


def start(interface, repo, node_store=None, seed_on_connect=True) -> None:
    """
    (Re)start the worker thread on each connection.
      1. Optionally drain interface.nodes cache for zero-OTA name resolution.
      2. Enqueue any DB nodes still missing a name.
    Clears previous session state on reconnect.
    """
    global _thread, _iface, _repo

    _stop_event.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=2)

    with _lock:
        _seen.clear()

    _iface = interface
    _repo  = repo
    _stop_event.clear()

    if seed_on_connect:
        _resolve_from_cache(interface, repo, node_store)

    try:
        rows = repo._conn.execute(
            "SELECT node_id FROM nodes WHERE short_name IS NULL"
        ).fetchall()
        for (node_id,) in rows:
            hex_id = f"!{node_id & 0xFFFFFFFF:08x}"
            if hex_id not in _SKIP_IDS:
                repo.enqueue_request(node_id)
                with _lock:
                    _seen.add(hex_id)
        if rows:
            log.info("Node requester: %d node(s) queued for OTA info requests", len(rows))
    except Exception as e:
        log.debug("Node requester: DB pre-fill skipped: %s", e)

    _thread = threading.Thread(target=_worker, daemon=True, name="node-requester")
    _thread.start()
    log.info("Node requester started (interval=%ds)", INTERVAL_S)


def stop() -> None:
    """Signal the worker thread to exit."""
    _stop_event.set()


def add(node_id_hex: str) -> None:
    """
    Queue a nodeinfo request for this node if not already seen this session.
    Safe to call from any thread (listener pubsub callback).
    """
    if node_id_hex in _SKIP_IDS:
        return
    with _lock:
        if node_id_hex in _seen:
            return
        _seen.add(node_id_hex)
    if _repo is None:
        return
    try:
        node_id = int(node_id_hex[1:], 16)
        _repo.enqueue_request(node_id)
        log.debug("Queued nodeinfo request for %s", node_id_hex)
    except Exception as e:
        log.debug("Node requester: enqueue failed for %s: %s", node_id_hex, e)


def ack(packet_id: int) -> None:
    """Routing ACK received — advance state to ACKED for this packet_id."""
    if _repo is None:
        return
    try:
        _repo.ack_by_packet_id(packet_id)
        log.debug("Node requester: ACK for packet_id=%d", packet_id)
    except Exception as e:
        log.debug("Node requester: ack failed: %s", e)


def nack(packet_id: int) -> None:
    """Routing NACK received — advance state to NACKED for retry with cooldown."""
    if _repo is None:
        return
    try:
        _repo.nack_by_packet_id(packet_id)
        log.debug("Node requester: NACK for packet_id=%d", packet_id)
    except Exception as e:
        log.debug("Node requester: nack failed: %s", e)


def requeue_unresolved(repo) -> None:
    """
    Re-queue all nodes in the DB that still have no short_name.
    Wipes and rebuilds the queue so every node gets a fresh PENDING entry.
    Called when the user clicks 'Repopulate Unresolved Names'.
    """
    with _lock:
        _seen.clear()
    try:
        repo.reset_queue()
        rows = repo._conn.execute(
            "SELECT node_id FROM nodes WHERE short_name IS NULL"
        ).fetchall()
        count = 0
        for (node_id,) in rows:
            hex_id = f"!{node_id & 0xFFFFFFFF:08x}"
            if hex_id not in _SKIP_IDS:
                repo.enqueue_request(node_id)
                with _lock:
                    _seen.add(hex_id)
                count += 1
        log.info("Node requester: re-queued %d unresolved node(s)", count)
    except Exception as e:
        log.warning("Node requester: requeue_unresolved failed: %s", e)


def _resolve_from_cache(interface, repo, node_store) -> None:
    """Hydrate names from the connected device's local node cache — no OTA cost."""
    try:
        cache = getattr(interface, "nodes", None) or {}
        updated = 0
        for node_id_hex, node_data in cache.items():
            user = node_data.get("user", {})
            short_name = user.get("shortName")
            if not short_name:
                continue
            if not (isinstance(node_id_hex, str) and node_id_hex.startswith("!")):
                continue
            try:
                node_id = int(node_id_hex[1:], 16)
            except ValueError:
                continue
            long_name = user.get("longName")
            hw_model  = user.get("hwModel")
            role = str(user.get("role")) if user.get("role") is not None else "CLIENT"
            repo.upsert_node(
                node_id,
                short_name=short_name,
                long_name=long_name,
                hardware_model=str(hw_model) if hw_model is not None else None,
                role=role,
            )
            if node_store is not None:
                node_store.upsert(
                    node_id,
                    short_name=short_name,
                    long_name=long_name,
                    hardware_model=str(hw_model) if hw_model is not None else None,
                    role=role,
                )
            updated += 1
        if updated:
            log.info("Node requester: resolved %d name(s) from interface cache", updated)
    except Exception as e:
        log.warning("Node requester: cache resolution failed: %s", e)


def _worker() -> None:
    while not _stop_event.is_set():
        if _iface and _repo:
            try:
                row = _repo.get_next_pending(NACK_COOLDOWN)
            except Exception as e:
                log.debug("Node requester: DB query failed: %s", e)
                row = None

            if row:
                node_id     = row["node_id"]
                node_id_hex = f"!{node_id & 0xFFFFFFFF:08x}"
                attempt_num = row["attempts"] + 1
                try:
                    from meshtastic import portnums_pb2
                    sent = _iface.sendData(
                        b"",
                        destinationId=node_id_hex,
                        portNum=portnums_pb2.PortNum.NODEINFO_APP,
                        wantAck=False,
                        wantResponse=True,
                    )
                    pkt_id = getattr(sent, "id", None) if sent is not None else None
                    if pkt_id:
                        _repo.mark_sent(node_id, pkt_id)
                        log.info(
                            "Requested nodeinfo from %s (attempt %d/%d, packet_id=%d)",
                            node_id_hex, attempt_num, MAX_ATTEMPTS, pkt_id,
                        )
                    else:
                        log.warning(
                            "Sent nodeinfo to %s but got no packet ID back", node_id_hex
                        )
                except Exception as e:
                    log.warning("Failed to request nodeinfo from %s: %s", node_id_hex, e)

        _stop_event.wait(INTERVAL_S)
