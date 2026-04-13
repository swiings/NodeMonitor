import math
import os
import sqlite3
import time

import bundle_paths
import sessions as _sessions


def get_conn() -> sqlite3.Connection:
    """Open a fresh read-only connection to the active session's DB.
    WAL mode is already set by NMListener. Creates the schema if the DB
    is brand-new (new session, NMListener hasn't connected yet)."""
    db_path = _sessions.get_db_path()
    return get_conn_for_path(db_path)


def get_conn_for_path(db_path: str) -> sqlite3.Connection:
    """Open a read-only connection to an arbitrary session DB by path."""
    _ensure_schema(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON;")
    conn.row_factory = sqlite3.Row
    _register_haversine(conn)
    return conn


def _ensure_schema(db_path: str) -> None:
    """Create tables if they don't exist. No-op on an established DB."""
    import sys
    listener_dir = bundle_paths.listener_source_dir()
    if listener_dir not in sys.path:
        sys.path.insert(0, listener_dir)
    try:
        from db.schema import create_tables
        conn = sqlite3.connect(db_path, check_same_thread=False)
        create_tables(conn)
        conn.close()
    except Exception:
        pass  # If schema creation fails, queries will return empty gracefully


def _register_haversine(conn: sqlite3.Connection) -> None:
    R = 6371.0

    def haversine(lat1, lon1, lat2, lon2):
        if None in (lat1, lon1, lat2, lon2):
            return None
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    conn.create_function("haversine", 4, haversine)


def get_my_node_position(conn, my_node_id) -> tuple:
    """Return (lat, lon) for the configured 'my node', or (None, None)."""
    if my_node_id is None:
        return None, None
    try:
        row = conn.execute(
            "SELECT lat, lon FROM nodes WHERE node_id = ? LIMIT 1", (my_node_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    if row and row["lat"] is not None and row["lon"] is not None:
        return row["lat"], row["lon"]
    return None, None


def get_all_nodes(conn, my_lat, my_lon) -> list[dict]:
    """
    Fetch all nodes enriched with packet counts, message counts, and distance.
    Single query; Python layer re-sorts for each telemetry list.
    Returns empty list if the DB has no tables yet (new session).
    """
    try:
        rows = conn.execute(
            """
            SELECT
                n.node_id,
                n.short_name,
                n.long_name,
                n.hardware_model,
                n.first_seen,
                n.last_heard,
                n.device_last_heard,
                n.seeded,
                n.lat,
                n.lon,
                n.altitude_m,
                n.rssi,
                n.snr,
                n.battery_level,
                n.voltage,
                n.hop_limit,
                n.role,
                COALESCE(pc.total_packets, 0)   AS total_packets,
                COALESCE(mc.sent_count, 0)      AS sent_count,
                COALESCE(mc.received_count, 0)  AS received_count,
                COALESCE(de.dead_end_count, 0)  AS dead_end_count,
                haversine(n.lat, n.lon, :my_lat, :my_lon) AS distance_km
            FROM nodes n
            LEFT JOIN (
                SELECT from_node_id, COUNT(*) AS total_packets
                FROM packets
                GROUP BY from_node_id
            ) pc ON pc.from_node_id = n.node_id
            LEFT JOIN message_counts mc ON mc.node_id = n.node_id
            LEFT JOIN (
                SELECT from_node_id, COUNT(*) AS dead_end_count
                FROM packets
                WHERE hop_limit = 0
                GROUP BY from_node_id
            ) de ON de.from_node_id = n.node_id
            ORDER BY n.last_heard DESC
            """,
            {"my_lat": my_lat, "my_lon": my_lon},
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [_enrich_node(dict(row)) for row in rows]


def _enrich_node(node: dict) -> dict:
    node["node_id_hex"] = f"!{node['node_id'] & 0xFFFFFFFF:08x}"
    return node


# Sort functions for each telemetry list. Tuple: (key_fn, reverse)
_SORT = {
    "last_heard": (lambda n: n["last_heard"] or 0,                             True),
    "nearby":     (lambda n: (n["distance_km"] is None, n["distance_km"] or 0), False),
    "big_sig":    (lambda n: (n["rssi"] is None, -(n["rssi"] or 0)),            False),
    "most_pkts":  (lambda n: n["total_packets"],                                True),
    "msgs_sent":  (lambda n: n["sent_count"],                                   True),
    "msgs_rcvd":  (lambda n: n["received_count"],                               True),
    "dead_end":   (lambda n: n["dead_end_count"],                               True),
}

LIST_TITLES = {
    "last_heard": "LAST HEARD",
    "nearby":     "NEARBY",
    "big_sig":    "BIG SIG",
    "most_pkts":  "MOST PKTS",
    "msgs_sent":  "MSGS SENT",
    "msgs_rcvd":  "MSGS RCVD",
    "dead_end":   "DEAD END",
}


_EXCLUDE_MY_NODE = {"last_heard", "nearby", "big_sig", "most_pkts", "dead_end"}


def build_telemetry_lists(nodes: list[dict], cfg: dict) -> list[dict]:
    """
    Given the full node list and settings, return configured telemetry lists
    each sorted and truncated to their configured limit.
    The reference node (my_node_id) is excluded from activity lists but
    kept in message lists where it's meaningful.
    """
    tl_cfg = cfg.get("telemetry_lists", {})
    enabled = tl_cfg.get("enabled_lists", [])
    limits = tl_cfg.get("limits", {})
    my_node_id = cfg.get("my_node_id")
    result = []
    for list_id in enabled:
        if list_id not in _SORT:
            continue
        key_fn, reverse = _SORT[list_id]
        limit = limits.get(list_id, 5)
        pool = [n for n in nodes if not (
            list_id in _EXCLUDE_MY_NODE
            and my_node_id is not None
            and (n["node_id"] & 0xFFFFFFFF) == (my_node_id & 0xFFFFFFFF)
        )]
        sorted_nodes = sorted(pool, key=key_fn, reverse=reverse)[:limit]
        result.append({
            "id": list_id,
            "title": LIST_TITLES.get(list_id, list_id.upper()),
            "nodes": sorted_nodes,
        })
    return result


def get_gauges(conn, my_node_id, my_lat, my_lon) -> dict:
    """Compute the five gauge values."""
    now = int(time.time())

    # Node counts
    total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    # Active: heard in the last hour
    active = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE last_heard > ?", (now - 3600,)
    ).fetchone()[0]
    # Direct: nodes from which we received a packet with no relay hops (hop_limit >= 3)
    direct = conn.execute(
        "SELECT COUNT(DISTINCT from_node_id) FROM packets WHERE hop_limit >= 3"
    ).fetchone()[0]

    # Packets per minute: average of last 5 complete minute buckets
    since_5min = ((now - 300) // 60) * 60
    ppm_row = conn.execute(
        "SELECT SUM(count) FROM packet_timeline WHERE minute_ts >= ?", (since_5min,)
    ).fetchone()
    ppm = round((ppm_row[0] or 0) / 5, 1)

    # Packets per hour: sum of last 60 minute buckets
    since_1hr = ((now - 3600) // 60) * 60
    pph_row = conn.execute(
        "SELECT SUM(count) FROM packet_timeline WHERE minute_ts >= ?", (since_1hr,)
    ).fetchone()
    pph = pph_row[0] or 0

    # Closest and farthest nodes — only needs a reference position, not necessarily my_node_id
    closest = None
    farthest = None
    if my_lat is not None and my_lon is not None:
        excl = my_node_id if my_node_id is not None else -1
        rows = conn.execute(
            """
            SELECT node_id, short_name,
                   haversine(lat, lon, :lat, :lon) AS dist_km
            FROM nodes
            WHERE lat IS NOT NULL AND lon IS NOT NULL
              AND node_id != :my_id
            ORDER BY dist_km ASC
            """,
            {"lat": my_lat, "lon": my_lon, "my_id": excl},
        ).fetchall()
        if rows:
            def _fmt(row):
                name = row["short_name"] or f"!{row['node_id'] & 0xFFFFFFFF:08x}"
                mi = round((row["dist_km"] or 0) * 0.621371, 1)
                return {"node_id": row["node_id"], "short_name": name, "distance_mi": mi}
            closest  = _fmt(rows[0])
            farthest = _fmt(rows[-1])

    # Session totals
    session_packets = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0] or 0
    first_pkt_ts    = conn.execute("SELECT MIN(rx_time) FROM packets").fetchone()[0]

    # Role counts: router-class vs client-class
    role_rows = conn.execute("SELECT role, COUNT(*) AS cnt FROM nodes GROUP BY role").fetchall()
    _router_roles = {"ROUTER", "ROUTER_CLIENT", "ROUTER_LATE", "REPEATER"}
    router_count = 0
    client_count = 0
    for row in role_rows:
        role = (row["role"] or "CLIENT").upper()
        if role in _router_roles:
            router_count += row["cnt"]
        else:
            client_count += row["cnt"]

    # Hop distribution: bucket hop_limit into 0, 1, 2, 3, 4+
    # Exclude my own node — its TCP packets carry hop_limit=3 but aren't RF mesh packets
    excl_hop = my_node_id if my_node_id is not None else -1
    hop_rows = conn.execute(
        "SELECT hop_limit, COUNT(*) AS cnt FROM packets WHERE hop_limit IS NOT NULL AND from_node_id != ? GROUP BY hop_limit",
        (excl_hop,)
    ).fetchall()
    hop_raw = {row["hop_limit"]: row["cnt"] for row in hop_rows}
    hop_dist = {
        "4+": sum(v for k, v in hop_raw.items() if k >= 4),
        "3":  hop_raw.get(3, 0),
        "2":  hop_raw.get(2, 0),
        "1":  hop_raw.get(1, 0),
        "0":  hop_raw.get(0, 0),
    }
    # Raw individual hop_limit counts for the dynamic list (no bucketing)
    hop_dist_raw = {str(k): v for k, v in sorted(hop_raw.items(), reverse=True)}

    return {
        "direct_count":       direct,
        "active_count":       active,
        "total_count":        total,
        "packets_per_minute": ppm,
        "packets_per_hour":   pph,
        "closest":            closest,
        "farthest":           farthest,
        "session_packets":    session_packets,
        "session_start_ts":   first_pkt_ts,
        "hop_dist":           hop_dist,
        "hop_dist_raw":       hop_dist_raw,
        "router_count":       router_count,
        "client_count":       client_count,
    }


def get_timeline(conn, time_scale_minutes: int, my_node_id=None) -> tuple[list[dict], int]:
    """
    Return (buckets, session_minutes).
    Each bucket: {minute_ts, total, messages, channel_util, by_portnum}
    by_portnum is a dict of {portnum: count} for each portnum in that minute.
    session_minutes is the span from first packet to now (min 15).
    """
    now = int(time.time())
    since_ts = now - time_scale_minutes * 60

    rows = conn.execute(
        """
        SELECT minute_ts, portnum, count
        FROM packet_timeline
        WHERE minute_ts >= ?
        ORDER BY minute_ts ASC
        """,
        (since_ts,),
    ).fetchall()

    # Aggregate per minute
    totals: dict[int, int] = {}
    messages: dict[int, int] = {}
    by_portnum: dict[int, dict[str, int]] = {}
    for row in rows:
        m = row["minute_ts"]
        p = row["portnum"] or "UNKNOWN"
        totals[m] = totals.get(m, 0) + row["count"]
        if p == "TEXT_MESSAGE_APP":
            messages[m] = messages.get(m, 0) + row["count"]
        if m not in by_portnum:
            by_portnum[m] = {}
        by_portnum[m][p] = by_portnum[m].get(p, 0) + row["count"]

    # Channel utilization from my node
    chan_util: dict[int, float] = {}
    if my_node_id is not None:
        try:
            util_rows = conn.execute(
                """
                SELECT (rx_time / 60) * 60 AS minute_ts,
                       channel_util
                FROM telemetry
                WHERE node_id = ?
                  AND rx_time >= ?
                  AND channel_util IS NOT NULL
                ORDER BY rx_time ASC
                """,
                (my_node_id, since_ts),
            ).fetchall()
            for row in util_rows:
                chan_util[row["minute_ts"]] = round(row["channel_util"], 1)
        except sqlite3.OperationalError:
            pass

    # Session span
    try:
        first_pkt = conn.execute("SELECT MIN(rx_time) FROM packets").fetchone()[0]
    except sqlite3.OperationalError:
        first_pkt = None
    if first_pkt:
        session_minutes = max(15, int((now - first_pkt) / 60) + 1)
    else:
        session_minutes = 15

    # Zero-fill every minute in the window
    now_min = (now // 60) * 60
    since_min = (since_ts // 60) * 60
    result = []
    t = since_min
    while t <= now_min:
        result.append({
            "minute_ts":    t,
            "total":        totals.get(t, 0),
            "messages":     messages.get(t, 0),
            "channel_util": chan_util.get(t, None),
            "by_portnum":   by_portnum.get(t, {}),
        })
        t += 60
    return result, session_minutes


def get_packet_breakdown(conn, my_node_id=None) -> list[dict]:
    """
    Return packet counts grouped by portnum, sorted by count descending.
    Reference node packets are excluded via my_node_id.
    UNKNOWN portnum = encrypted / undecodeable packets.
    """
    excl = my_node_id if my_node_id is not None else -1
    try:
        rows = conn.execute(
            """
            SELECT portnum, COUNT(*) AS cnt
            FROM packets
            WHERE from_node_id != :excl
            GROUP BY portnum
            ORDER BY cnt DESC
            """,
            {"excl": excl},
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [{"portnum": row["portnum"], "count": row["cnt"]} for row in rows]
