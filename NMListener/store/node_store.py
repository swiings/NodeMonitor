import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NodeRecord:
    node_id: int
    short_name: Optional[str] = None
    long_name: Optional[str] = None
    hardware_model: Optional[str] = None
    first_seen: int = field(default_factory=lambda: int(time.time()))
    last_heard: int = field(default_factory=lambda: int(time.time()))
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[float] = None
    rssi: Optional[int] = None
    snr: Optional[float] = None
    battery_level: Optional[int] = None
    voltage: Optional[float] = None
    hop_limit: Optional[int] = None
    role: Optional[str] = None


class NodeStore:
    """In-memory registry of all mesh nodes seen during this session."""

    def __init__(self) -> None:
        self._nodes: dict[int, NodeRecord] = {}
        self._lock = threading.Lock()

    def upsert(self, node_id: int, **fields) -> NodeRecord:
        with self._lock:
            if node_id not in self._nodes:
                self._nodes[node_id] = NodeRecord(node_id=node_id)
            record = self._nodes[node_id]
            for key, value in fields.items():
                if value is not None and hasattr(record, key):
                    setattr(record, key, value)
            record.last_heard = int(time.time())
            return record

    def get(self, node_id: int) -> Optional[NodeRecord]:
        with self._lock:
            return self._nodes.get(node_id)

    def all_nodes(self) -> list[NodeRecord]:
        with self._lock:
            return list(self._nodes.values())

    def node_count(self) -> int:
        with self._lock:
            return len(self._nodes)
