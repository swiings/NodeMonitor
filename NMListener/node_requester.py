"""
node_requester.py — Background queue that actively requests node info
for nodes heard on the mesh that have no short_name yet.

One NODEINFO_APP request with want_response=True is sent every INTERVAL_S
seconds so we don't flood the channel.  The response arrives as a normal
NODEINFO_APP packet and is processed by the existing handle_nodeinfo() path.

Usage:
    node_requester.start(interface, repo)   # after connect / seeding
    node_requester.add("!deadbeef")         # from packet receive hook
    node_requester.stop()                   # on disconnect / shutdown
"""

import logging
import threading
from collections import deque

log = logging.getLogger(__name__)

INTERVAL_S = 30  # seconds between outbound requests

_queue: deque = deque()
_requested: set = set()   # node_id_hex strings already queued this session
_lock = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_iface = None

# Broadcast/all-nodes IDs — never request these
_SKIP_IDS = {"!ffffffff", "!00000000"}


def start(interface, repo, node_store=None, seed_on_connect=True) -> None:
    """
    Start the worker thread.  On each connect:
      1. If seed_on_connect: drain interface.nodes (HAXX's local cache) —
         zero OTA traffic, resolves most nodes instantly.
      2. Any still-nameless nodes in the DB are queued for OTA requests as a
         fallback for nodes HAXX hasn't heard from directly.
    Clears previous session state so reconnects start fresh.
    """
    global _thread, _iface

    _stop_event.set()  # stop any previous worker
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=2)

    # Reset state for this session
    with _lock:
        _queue.clear()
        _requested.clear()

    _iface = interface
    _stop_event.clear()

    # Step 1: resolve names from the connected node's local cache (no OTA)
    # Skip if seeding is disabled — the user wants a clean live-only session
    if seed_on_connect:
        _resolve_from_cache(interface, repo, node_store)

    # Step 2: queue OTA requests for any nodes still missing a name
    try:
        rows = repo._conn.execute(
            "SELECT node_id FROM nodes WHERE short_name IS NULL"
        ).fetchall()
        for row in rows:
            _enqueue(f"!{row[0] & 0xFFFFFFFF:08x}")
        if _queue:
            log.info("Node requester: %d node(s) queued for OTA info requests", len(_queue))
    except Exception as e:
        log.debug("Node requester: DB pre-fill skipped: %s", e)

    _thread = threading.Thread(target=_worker, daemon=True, name="node-requester")
    _thread.start()
    log.info("Node requester started (interval=%ds)", INTERVAL_S)


def _resolve_from_cache(interface, repo, node_store) -> None:
    """
    Walk interface.nodes (the connected node's in-memory node list) and write
    any names we're missing directly to the DB and NodeStore.  No packets sent.
    """
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
            role = user.get("role")
            role = str(role) if role is not None else "CLIENT"

            repo.upsert_node(
                node_id,
                short_name=short_name,
                long_name=long_name,
                hardware_model=str(hw_model) if hw_model is not None else None,
                role=role,
            )
            if node_store is not None:
                node_store.upsert(node_id, short_name=short_name, long_name=long_name,
                                  hardware_model=str(hw_model) if hw_model is not None else None,
                                  role=role)
            updated += 1

        if updated:
            log.info("Node requester: resolved %d name(s) from interface cache", updated)
    except Exception as e:
        log.warning("Node requester: cache resolution failed: %s", e)


def stop() -> None:
    """Signal the worker thread to exit."""
    _stop_event.set()


def add(node_id_hex: str) -> None:
    """
    Queue a nodeinfo request for node_id_hex if not already requested
    this session.  Safe to call from any thread.
    """
    _enqueue(node_id_hex)


def requeue_unresolved(repo) -> None:
    """
    Re-queue all nodes in the DB that still have no short_name.
    Clears the session's 'already requested' set first so these nodes
    get another chance even if they were tried before.  Called when the
    user clicks 'Repopulate Unresolved Names' in the settings panel.
    """
    with _lock:
        _requested.clear()
    try:
        rows = repo._conn.execute(
            "SELECT node_id FROM nodes WHERE short_name IS NULL"
        ).fetchall()
        count = 0
        for row in rows:
            _enqueue(f"!{row[0] & 0xFFFFFFFF:08x}")
            count += 1
        log.info("Node requester: re-queued %d unresolved node(s)", count)
    except Exception as e:
        log.warning("Node requester: requeue_unresolved failed: %s", e)


def _enqueue(node_id_hex: str) -> None:
    if node_id_hex in _SKIP_IDS:
        return
    with _lock:
        if node_id_hex not in _requested:
            _requested.add(node_id_hex)
            _queue.append(node_id_hex)
            log.debug("Queued nodeinfo request for %s (depth=%d)", node_id_hex, len(_queue))


def _worker() -> None:
    while not _stop_event.is_set():
        node_id_hex = None
        with _lock:
            if _queue:
                node_id_hex = _queue.popleft()

        if node_id_hex and _iface:
            try:
                from meshtastic import portnums_pb2
                _iface.sendData(
                    b"",
                    destinationId=node_id_hex,
                    portNum=portnums_pb2.PortNum.NODEINFO_APP,
                    wantAck=False,
                    wantResponse=True,
                )
                with _lock:
                    remaining = len(_queue)
                log.info("Requested nodeinfo from %s (%d remaining in queue)", node_id_hex, remaining)
            except Exception as e:
                log.warning("Failed to request nodeinfo from %s: %s", node_id_hex, e)

        _stop_event.wait(INTERVAL_S)
