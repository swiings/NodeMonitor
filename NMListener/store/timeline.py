import threading
import time
from collections import defaultdict


class PacketTimeline:
    """
    Tracks packets per minute in-memory, bucketed by minute boundary.
    Bucket key = Unix timestamp of the start of that minute (ts - ts % 60).
    """

    def __init__(self) -> None:
        # {minute_ts: {portnum: count}}
        self._buckets: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def record(self, portnum: str, timestamp: int | None = None) -> int:
        """Increment the count for the packet's minute bucket. Returns the minute_ts."""
        ts = timestamp or int(time.time())
        minute_ts = ts - (ts % 60)
        with self._lock:
            self._buckets[minute_ts][portnum] += 1
        return minute_ts

    def get_recent(self, minutes: int = 60) -> list[dict]:
        """Return per-minute bucket data for the last N minutes, sorted oldest-first."""
        cutoff = int(time.time()) - (minutes * 60)
        with self._lock:
            result = []
            for minute_ts, counts in sorted(self._buckets.items()):
                if minute_ts >= cutoff:
                    result.append({
                        "minute_ts": minute_ts,
                        "total": sum(counts.values()),
                        "by_portnum": dict(counts),
                    })
        return result

    def pop_stale(self, older_than_minutes: int = 2) -> list[tuple[int, str, int]]:
        """
        Remove and return buckets older than N minutes as (minute_ts, portnum, count) tuples.
        Call this periodically to flush stale buckets to the DB.
        """
        cutoff = int(time.time()) - (older_than_minutes * 60)
        rows = []
        with self._lock:
            stale_keys = [k for k in self._buckets if k < cutoff]
            for key in stale_keys:
                for portnum, count in self._buckets.pop(key).items():
                    rows.append((key, portnum, count))
        return rows
