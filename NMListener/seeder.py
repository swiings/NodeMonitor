"""
seeder.py — Seeds the node database from the Meshtastic node's own node list.

Called once per connection, immediately after the interface is established.
Pulls interface.nodes (the device's full known-node table) and upserts each
entry with seeded=1 and device_last_heard from the device's own tracking.

Key difference from live packet handling:
- Positions here use latitudeI / longitudeI (integer, degrees * 1e7) — NOT float degrees.
- device_last_heard is the Meshtastic device's timestamp, which may be days old.
"""

import logging
import time
from typing import Optional

from db.repository import Repository
from store.node_store import NodeStore

log = logging.getLogger(__name__)


def seed_nodes(interface, repo: Repository, node_store: NodeStore) -> int:
    """
    Upsert all nodes from the interface's node database into our DB.
    Returns the number of nodes processed.
    """
    nodes = getattr(interface, "nodes", None) or {}
    count = 0

    for node_key, node in nodes.items():
        try:
            node_id = node.get("num")
            if node_id is None:
                continue

            user = node.get("user", {})
            position = node.get("position", {})
            device_metrics = node.get("deviceMetrics", {})

            short_name = user.get("shortName")
            long_name = user.get("longName")
            hw_model = user.get("hwModel")
            if hw_model is not None:
                hw_model = str(hw_model)
            # protobuf3 omits default-value fields; CLIENT = 0 is the default role
            role = user.get("role")
            role = str(role) if role is not None else "CLIENT"

            # Positions in interface.nodes use integer coords (degrees * 1e7)
            lat = _int_coord(position.get("latitudeI"))
            lon = _int_coord(position.get("longitudeI"))
            alt = position.get("altitude")
            alt = float(alt) if alt is not None else None

            device_last_heard = node.get("lastHeard")
            snr = node.get("snr")

            battery = device_metrics.get("batteryLevel")
            voltage = device_metrics.get("voltage")

            node_store.upsert(
                node_id,
                short_name=short_name,
                long_name=long_name,
                hardware_model=hw_model,
                lat=lat,
                lon=lon,
                altitude_m=alt,
                snr=snr,
                battery_level=battery,
                voltage=voltage,
                role=role,
            )

            repo.upsert_node(
                node_id,
                short_name=short_name,
                long_name=long_name,
                hardware_model=hw_model,
                last_heard=int(time.time()),
                device_last_heard=device_last_heard,
                seeded=1,
                lat=lat,
                lon=lon,
                altitude_m=alt,
                snr=snr,
                battery_level=battery,
                voltage=voltage,
                role=role,
            )
            count += 1

        except Exception:
            log.exception("Error seeding node %s", node_key)

    log.info("Seeded %d node(s) from device node list", count)
    return count


def _int_coord(value) -> Optional[float]:
    """Convert Meshtastic integer coordinate (degrees * 1e7) to float degrees."""
    if value is None or value == 0:
        return None
    return value / 1e7
