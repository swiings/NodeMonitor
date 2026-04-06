import threading
from collections import defaultdict


class MessageCounter:
    """Tracks per-node sent and received message counts in memory."""

    def __init__(self) -> None:
        self._sent: dict[int, int] = defaultdict(int)
        self._received: dict[int, int] = defaultdict(int)
        self._lock = threading.Lock()

    def record_sent(self, node_id: int) -> None:
        with self._lock:
            self._sent[node_id] += 1

    def record_received(self, node_id: int) -> None:
        with self._lock:
            self._received[node_id] += 1

    def get_counts(self, node_id: int) -> dict:
        with self._lock:
            return {"sent": self._sent[node_id], "received": self._received[node_id]}

    def all_counts(self) -> dict[int, dict]:
        with self._lock:
            node_ids = set(self._sent) | set(self._received)
            return {
                nid: {"sent": self._sent[nid], "received": self._received[nid]}
                for nid in node_ids
            }
