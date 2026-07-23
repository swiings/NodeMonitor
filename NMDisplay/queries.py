from __future__ import annotations

import math
import os
import sqlite3
import time
import json
import urllib.parse
import urllib.request

import bundle_paths
import sessions as _sessions


_VEHICLE_TIMEOUT_S = 36000
_AIRCRAFT_ALTITUDE_FT = 20000.0
_AIRCRAFT_ALTITUDE_M = _AIRCRAFT_ALTITUDE_FT / 3.28084
_OSRM_MATCH_URL = os.environ.get("NM_OSRM_MATCH_URL", "https://router.project-osrm.org/match/v1/driving/")
_OSRM_TIMEOUT_S = float(os.environ.get("NM_OSRM_TIMEOUT_S", "3.5"))
_OSRM_MAX_POINTS = int(os.environ.get("NM_OSRM_MAX_POINTS", "100"))


def get_conn() -> sqlite3.Connection:
    """Open a fresh read-only connection to the active session's DB.
    WAL mode is already set by NMListener. Creates the schema if the DB
    is brand-new (new session, NMListener hasn't connected yet)."""
    db_path = _sessions.get_db_path()
    return get_conn_for_path(db_path)


def get_rw_conn() -> sqlite3.Connection:
    """Open a writable connection to the active session DB."""
    db_path = _sessions.get_db_path()
    _ensure_schema(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _register_haversine(conn)
    return conn


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
                n.source,
                n.sats,
                n.rssi,
                n.snr,
                n.battery_level,
                n.voltage,
                COALESCE(lp.last_hop_limit, n.hop_limit) AS hop_limit,
                lp.last_hop_start AS hop_start,
                CASE
                    WHEN lp.last_hop_start IS NOT NULL AND lp.last_hop_start >= COALESCE(lp.last_hop_limit, 0)
                    THEN lp.last_hop_start - COALESCE(lp.last_hop_limit, 0)
                    ELSE NULL
                END AS hops_away,
                n.packet_source,
                n.role,
                n.last_motion_ts,
                n.motion_eval_ts,
                n.motion_score,
                n.street,
                n.city,
                n.state,
                lpos.precision_bits,
                COALESCE(pc.total_packets, 0)   AS total_packets,
                COALESCE(mc.sent_count, 0)      AS sent_count,
                COALESCE(mc.received_count, 0)  AS received_count,
                COALESCE(de.dead_end_count, 0)  AS dead_end_count,
                haversine(n.lat, n.lon, :my_lat, :my_lon) AS distance_km,
                lp.last_portnum
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
            LEFT JOIN (
                SELECT p.from_node_id,
                       p.portnum AS last_portnum,
                       p.hop_limit AS last_hop_limit,
                       p.hop_start AS last_hop_start
                FROM packets p
                WHERE p.id = (
                    SELECT p2.id
                    FROM packets p2
                    WHERE p2.from_node_id = p.from_node_id
                    ORDER BY p2.rx_time DESC, p2.id DESC
                    LIMIT 1
                )
            ) lp ON lp.from_node_id = n.node_id
            LEFT JOIN (
                SELECT node_id, precision_bits
                FROM positions
                WHERE id IN (SELECT MAX(id) FROM positions GROUP BY node_id)
            ) lpos ON lpos.node_id = n.node_id
            ORDER BY n.last_heard DESC
            """,
            {"my_lat": my_lat, "my_lon": my_lon},
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    now_ts = int(time.time())
    return [_enrich_node(dict(row), now_ts=now_ts) for row in rows]


def _enrich_node(node: dict, now_ts: int | None = None) -> dict:
    now = now_ts if now_ts is not None else int(time.time())
    base_role = node.get("role")
    altitude_m = node.get("altitude_m")
    is_airborne = altitude_m is not None and float(altitude_m) >= _AIRCRAFT_ALTITUDE_M
    last_motion_ts = node.get("last_motion_ts")
    moving_recently = (
        last_motion_ts is not None
        and (now - int(last_motion_ts)) < _VEHICLE_TIMEOUT_S
    )
    node["base_role"] = base_role
    node["is_airborne"] = is_airborne
    node["is_moving"] = moving_recently
    if is_airborne:
        node["role"] = "AIRCRAFT"
    elif moving_recently:
        node["role"] = "VEHICLE"
    node["node_id_hex"] = f"!{node['node_id'] & 0xFFFFFFFF:08x}"
    return node


# Sort functions for each telemetry list. Tuple: (key_fn, reverse)
_SORT = {
    "last_heard": (lambda n: n["last_heard"] or 0,                             True),
    "nearby":     (lambda n: (n["distance_km"] is None, n["distance_km"] or 0), False),
    "big_sig":    (lambda n: (n["rssi"] is None, -(n["rssi"] or 0)),            False),
    "lil_sig":    (lambda n: (n["rssi"] is None, n["rssi"] or 0),               False),
    "most_pkts":  (lambda n: n["total_packets"],                                True),
    "msgs_sent":  (lambda n: n["sent_count"],                                   True),
    "msgs_rcvd":  (lambda n: n["received_count"],                               True),
    "dead_end":   (lambda n: n["dead_end_count"],                               True),
}

LIST_TITLES = {
    "last_heard": "LAST HEARD",
    "nearby":     "NEARBY",
    "big_sig":    "BIG SIG",
    "lil_sig":    "LIL SIG",
    "most_pkts":  "MOST PKTS",
    "msgs_sent":  "MSGS SENT",
    "msgs_rcvd":  "MSGS RCVD",
    "dead_end":   "DEAD END",
}


_EXCLUDE_MY_NODE = {"last_heard", "nearby", "big_sig", "lil_sig", "most_pkts", "dead_end"}


def build_telemetry_lists(nodes: list[dict], cfg: dict) -> list[dict]:
    """
    Given the full node list and settings, return configured telemetry lists
    each sorted and truncated to their configured limit.
    The reference node (my_node_id) is excluded from activity lists but
    kept in message lists where it's meaningful.
    """
    tl_cfg = cfg.get("telemetry_lists", {})
    enabled = list(tl_cfg.get("enabled_lists", []))
    if "big_sig" in enabled and "lil_sig" not in enabled:
        idx = enabled.index("big_sig") + 1
        enabled.insert(idx, "lil_sig")
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
        # Always return up to 20 nodes so the client can increase the display
        # count via drag without a settings round-trip. The configured limit is
        # passed as "limit" so the client knows the saved default.
        sorted_nodes = sorted(pool, key=key_fn, reverse=reverse)[:20]
        entry = {
            "id":    list_id,
            "title": LIST_TITLES.get(list_id, list_id.upper()),
            "limit": limit,
            "nodes": sorted_nodes,
        }
        if list_id == "nearby":
            farthest_nodes = sorted(
                pool,
                key=lambda n: (n.get("distance_km") is None, -(n.get("distance_km") or 0)),
            )[:20]
            entry["farthest_nodes"] = farthest_nodes
        result.append(entry)
    return result


def get_gauges(conn, my_node_id, my_lat, my_lon, precomputed_nodes=None) -> dict:
    """Compute the five gauge values."""
    now = int(time.time())

    # Node counts
    total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    # Active: heard in the last 65 minutes (buffer for nodes that broadcast nodeinfo hourly)
    active = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE last_heard > ?", (now - 3900,)
    ).fetchone()[0]
    # Direct: hops-away = 0, computed from packet-level hop_start - hop_limit.
    # If the session has no hop_start data yet, fall back to legacy hop_limit-only heuristic.
    has_hop_start = conn.execute(
        "SELECT COUNT(*) FROM packets WHERE hop_start IS NOT NULL"
    ).fetchone()[0]
    if has_hop_start:
        direct = conn.execute(
            """
            SELECT COUNT(DISTINCT from_node_id)
            FROM packets
            WHERE hop_start IS NOT NULL
              AND hop_start >= COALESCE(hop_limit, 0)
              AND (hop_start - COALESCE(hop_limit, 0)) = 0
            """
        ).fetchone()[0]
    else:
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

    # Average RF signal over the last hour. AVG() ignores NULLs, so the
    # reference node's self-echo packets (null rssi/snr) are excluded for free.
    avg_rssi_1h = avg_snr_1h = None
    try:
        sig_row = conn.execute(
            "SELECT AVG(rx_rssi), AVG(rx_snr) FROM packets WHERE rx_time >= ?",
            (now - 3600,),
        ).fetchone()
        if sig_row:
            avg_rssi_1h = round(sig_row[0]) if sig_row[0] is not None else None
            avg_snr_1h  = round(sig_row[1], 1) if sig_row[1] is not None else None
    except sqlite3.OperationalError:
        pass

    # Closest and farthest nodes — only needs a reference position, not necessarily my_node_id
    closest = None
    farthest = None
    if my_lat is not None and my_lon is not None:
        excl = my_node_id if my_node_id is not None else -1
        source_nodes = precomputed_nodes if precomputed_nodes is not None else get_all_nodes(conn, my_lat, my_lon)
        rows = [
            r for r in source_nodes
            if r.get("node_id") != excl and r.get("distance_km") is not None
        ]
        rows.sort(key=lambda r: r["distance_km"])
        if rows:
            def _fmt(row):
                name = row["short_name"] or f"!{row['node_id'] & 0xFFFFFFFF:08x}"
                mi = round((row["distance_km"] or 0) * 0.621371, 1)
                hops_away = row.get("hops_away")
                return {"node_id": row["node_id"], "short_name": name, "distance_mi": mi, "role": row["role"], "hops_away": hops_away}
            closest  = _fmt(rows[0])
            farthest = _fmt(rows[-1])

    # Session totals shown in the gauge should match chart packet scope
    # (exclude packets originating from the local reference node).
    excl_pkts = my_node_id if my_node_id is not None else -1
    session_packets = conn.execute(
        "SELECT COUNT(*) FROM packets WHERE from_node_id != ?",
        (excl_pkts,),
    ).fetchone()[0] or 0
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

    # Hop-left distribution from packet hop_limit.
    # Exclude my own node — its TCP packets carry defaults that aren't RF mesh traversals.
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
    # Legacy sessions may contain NULL hop_limit (protobuf omitted zero in older flow).
    # Keep counting them and expose as "?" for compatibility.
    null_hop_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM packets WHERE hop_limit IS NULL AND from_node_id != ?",
        (excl_hop,)
    ).fetchone()
    null_hop_count = null_hop_row["cnt"] if null_hop_row else 0

    # Raw individual hop_limit counts for the dynamic list (no bucketing)
    hop_dist_raw = {str(k): v for k, v in sorted(hop_raw.items(), reverse=True)}
    if null_hop_count:
        hop_dist_raw["?"] = null_hop_count

    # Hops-used distribution using packet-level hop_start - hop_limit when valid.
    used_rows = conn.execute(
        """
        SELECT (hop_start - COALESCE(hop_limit, 0)) AS hops_used, COUNT(*) AS cnt
        FROM packets
        WHERE from_node_id != ?
          AND hop_start IS NOT NULL
          AND hop_start >= COALESCE(hop_limit, 0)
        GROUP BY hops_used
        ORDER BY hops_used DESC
        """,
        (excl_hop,)
    ).fetchall()
    hop_used_raw = {str(row["hops_used"]): row["cnt"] for row in used_rows}

    # Hop usage percentage buckets in 10% increments.
    # usage% = ((hop_start - hop_limit) / hop_start) * 100
    # Example: start=3,left=1 => 66%, bucketed to 60.
    usage_pair_rows = conn.execute(
        """
        SELECT hop_start, COALESCE(hop_limit, 0) AS hop_left, COUNT(*) AS cnt
        FROM packets
        WHERE from_node_id != ?
          AND hop_start IS NOT NULL
          AND hop_start > 0
          AND hop_start >= COALESCE(hop_limit, 0)
        GROUP BY hop_start, hop_left
        """,
        (excl_hop,),
    ).fetchall()
    hop_usage_pct_buckets = {str(i): 0 for i in range(0, 101, 10)}
    for row in usage_pair_rows:
        hop_start = row["hop_start"]
        hop_left = row["hop_left"]
        cnt = row["cnt"] or 0
        if not hop_start:
            continue
        pct = ((hop_start - hop_left) / hop_start) * 100.0
        pct = max(0.0, min(100.0, pct))
        bucket = int(pct // 10) * 10
        if bucket > 100:
            bucket = 100
        hop_usage_pct_buckets[str(bucket)] += cnt

    # Raw hop efficiency rows grouped by hop_start and hops left.
    hop_efficiency_rows = conn.execute(
        """
        SELECT hop_start, COALESCE(hop_limit, 0) AS hop_left, COUNT(*) AS cnt
        FROM packets
        WHERE from_node_id != ?
          AND hop_start IS NOT NULL
          AND hop_start > 0
          AND hop_start >= COALESCE(hop_limit, 0)
        GROUP BY hop_start, hop_left
        ORDER BY hop_start DESC, hop_left ASC
        """,
        (excl_hop,),
    ).fetchall()
    hop_efficiency_raw: dict[str, dict[str, int]] = {}
    for row in hop_efficiency_rows:
        hop_start = str(row["hop_start"])
        hop_left = str(row["hop_left"])
        hop_efficiency_raw.setdefault(hop_start, {})[hop_left] = row["cnt"] or 0

    return {
        "direct_count":       direct,
        "active_count":       active,
        "total_count":        total,
        "packets_per_minute": ppm,
        "packets_per_hour":   pph,
        "avg_rssi_1h":        avg_rssi_1h,
        "avg_snr_1h":         avg_snr_1h,
        "closest":            closest,
        "farthest":           farthest,
        "session_packets":    session_packets,
        "session_start_ts":   first_pkt_ts,
        "hop_dist":           hop_dist,
        "hop_dist_raw":       hop_dist_raw,
        "hop_used_raw":       hop_used_raw,
        "hop_usage_pct_buckets": hop_usage_pct_buckets,
        "hop_efficiency_raw": hop_efficiency_raw,
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
    Reference node packets are excluded via my_node_id, and the reference
    node's periodic TELEMETRY_APP self-reports are ignored here so the Packet
    Breakdown card reflects peer traffic rather than the local serial stream.
    UNKNOWN portnum = encrypted / undecodeable packets.
    """
    excl = my_node_id if my_node_id is not None else -1
    try:
        rows = conn.execute(
            """
            SELECT portnum, COUNT(*) AS cnt
            FROM (
                SELECT CASE
                    WHEN portnum = 'UNKNOWN' AND to_node_id = 4294967295 THEN 'UNKNOWN_PRIVATE_CH'
                    WHEN portnum = 'UNKNOWN' AND to_node_id IS NOT NULL  THEN 'UNKNOWN_DIRECT'
                    ELSE portnum
                END AS portnum
                FROM packets
                WHERE from_node_id != :excl
                  AND NOT (
                      portnum = 'TELEMETRY_APP' AND from_node_id = :excl
                  )
            )
            GROUP BY portnum
            ORDER BY cnt DESC
            """,
            {"excl": excl},
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    return [{"portnum": row["portnum"], "count": row["cnt"]} for row in rows]


def get_hardware_breakdown(conn) -> list[dict]:
    """Return node counts grouped by hardware_model, sorted by count descending."""
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(hardware_model, 'Unknown') AS hw, COUNT(*) AS cnt
            FROM nodes
            GROUP BY hw
            ORDER BY cnt DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"hardware_model": row["hw"], "count": row["cnt"]} for row in rows]


def get_role_breakdown(conn) -> list[dict]:
    """Return node counts grouped by role, sorted by count descending."""
    now = int(time.time())
    try:
        rows = conn.execute(
            """
            SELECT
                CASE
                    WHEN last_motion_ts IS NOT NULL AND (? - last_motion_ts) < ? THEN 'VEHICLE'
                    ELSE COALESCE(role, 'Unknown')
                END AS role,
                COUNT(*) AS cnt
            FROM nodes
            GROUP BY role
            ORDER BY cnt DESC
            """
            ,
            (now, _VEHICLE_TIMEOUT_S),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"role": row["role"], "count": row["cnt"]} for row in rows]


def get_filtered_traffic(conn, by: str, value, limit: int = 200, my_node_id=None) -> list[dict]:
    """Return packets filtered by portnum / hardware_model / role / node_id."""
    base = """
         SELECT p.id, p.rx_time, p.from_node_id, p.to_node_id, p.portnum,
                         p.rx_rssi, p.rx_snr, p.hop_limit, p.hop_start,
               n.short_name, n.long_name,
             n.packet_source,
               pos.source, pos.sats
        FROM packets p
        LEFT JOIN nodes n ON n.node_id = p.from_node_id
        LEFT JOIN positions pos ON pos.packet_id = p.id
    """
    excl = my_node_id if my_node_id is not None else -1
    try:
        if by == "portnum":
            if value == "UNKNOWN_PRIVATE_CH":
                sql = base + "WHERE p.portnum = 'UNKNOWN' AND p.to_node_id = 4294967295 ORDER BY p.rx_time DESC LIMIT ?"
                rows = conn.execute(sql, (limit,)).fetchall()
            elif value == "UNKNOWN_DIRECT":
                sql = base + "WHERE p.portnum = 'UNKNOWN' AND p.to_node_id IS NOT NULL AND p.to_node_id != 4294967295 ORDER BY p.rx_time DESC LIMIT ?"
                rows = conn.execute(sql, (limit,)).fetchall()
            elif value == "TELEMETRY_APP" and my_node_id is not None:
                sql = base + "WHERE p.portnum = 'TELEMETRY_APP' AND p.from_node_id != :excl ORDER BY p.rx_time DESC LIMIT :limit"
                rows = conn.execute(sql, {"excl": excl, "limit": limit}).fetchall()
            else:
                sql = base + "WHERE p.portnum = ? ORDER BY p.rx_time DESC LIMIT ?"
                rows = conn.execute(sql, (str(value), limit)).fetchall()
        elif by == "hardware":
            sql = base + "WHERE COALESCE(n.hardware_model, 'Unknown') = :value AND NOT (p.portnum = 'TELEMETRY_APP' AND p.from_node_id = :excl) ORDER BY p.rx_time DESC LIMIT :limit"
            rows = conn.execute(sql, {"value": str(value), "excl": excl, "limit": limit}).fetchall()
        elif by == "role":
            sql = base + "WHERE COALESCE(n.role, 'Unknown') = :value AND NOT (p.portnum = 'TELEMETRY_APP' AND p.from_node_id = :excl) ORDER BY p.rx_time DESC LIMIT :limit"
            rows = conn.execute(sql, {"value": str(value), "excl": excl, "limit": limit}).fetchall()
        elif by == "node":
            sql = base + "WHERE p.from_node_id = ? ORDER BY p.rx_time DESC LIMIT ?"
            rows = conn.execute(sql, (int(value), limit)).fetchall()
        else:
            return []
    except (sqlite3.OperationalError, TypeError, ValueError):
        return []

    result = []
    for row in rows:
        d = dict(row)
        node_id = d.get("from_node_id")
        d["from_node_id_hex"] = f"!{node_id & 0xFFFFFFFF:08x}" if node_id else "—"
        d["from_display"] = d.get("short_name") or d["from_node_id_hex"]
        result.append(d)
    return result


def get_packet_detail(conn, packet_id: int) -> dict | None:
    """Return full decoded payload for a single packet, joining related tables."""
    import json as _json
    row = conn.execute(
        """SELECT p.id, p.rx_time, p.from_node_id, p.to_node_id, p.portnum,
                p.rx_rssi, p.rx_snr, p.hop_limit, p.hop_start, p.payload_json,
                  n.short_name, n.long_name, n.packet_source
           FROM packets p
           LEFT JOIN nodes n ON n.node_id = p.from_node_id
           WHERE p.id = ?""",
        (packet_id,)
    ).fetchone()
    if row is None:
        return None

    d = dict(row)
    node_id = d.get("from_node_id")
    d["from_node_id_hex"] = f"!{node_id & 0xFFFFFFFF:08x}" if node_id else "—"

    if d.get("payload_json"):
        try:
            d["payload"] = _json.loads(d["payload_json"])
        except Exception:
            d["payload"] = None
    else:
        d["payload"] = None
    del d["payload_json"]

    tel = conn.execute(
        """SELECT battery_level, voltage, channel_util, air_util_tx,
                  temperature, relative_humidity, barometric_pressure, gas_resistance
           FROM telemetry WHERE packet_id = ? LIMIT 1""",
        (packet_id,)
    ).fetchone()
    d["telemetry"] = dict(tel) if tel else None

    pos = conn.execute(
        "SELECT lat, lon, altitude_m, source, sats, precision_bits FROM positions WHERE packet_id = ? LIMIT 1",
        (packet_id,)
    ).fetchone()
    d["position"] = dict(pos) if pos else None

    msg = conn.execute(
        "SELECT message_text, to_node_id FROM text_messages WHERE packet_id = ? LIMIT 1",
        (packet_id,)
    ).fetchone()
    d["text_message"] = dict(msg) if msg else None

    return d


def delete_node(node_id: int) -> None:
    """Delete a node and all its associated data from the active session DB."""
    db_path = _sessions.get_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM message_counts WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM telemetry WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM positions WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM text_messages WHERE from_node_id = ?", (node_id,))
        conn.execute("DELETE FROM packets WHERE from_node_id = ?", (node_id,))
        conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        conn.commit()
    finally:
        conn.close()


def get_packets_for_export(conn) -> list[dict]:
    """Return all packet rows (excluding raw payload blobs) for CSV export."""
    try:
        rows = conn.execute(
            """
             SELECT p.id, p.rx_time, p.from_node_id, p.to_node_id, p.portnum,
                                 p.hop_limit, p.hop_start, p.rx_rssi, p.rx_snr, p.rx_lat, p.rx_lon,
                 n.packet_source,
                   pos.source, pos.sats
            FROM packets p
             LEFT JOIN nodes n ON n.node_id = p.from_node_id
            LEFT JOIN positions pos ON pos.packet_id = p.id
            ORDER BY rx_time ASC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    result = []
    for row in rows:
        d = dict(row)
        d["from_node_id"] = f"!{d['from_node_id'] & 0xFFFFFFFF:08x}" if d["from_node_id"] else "—"
        d["to_node_id"]   = f"!{d['to_node_id']   & 0xFFFFFFFF:08x}" if d["to_node_id"]   else "—"
        result.append(d)
    return result


def create_track_from_node(conn, node_id: int, *, color: str = "#ff9f1a") -> dict | None:
    """Persist a new track row from all known positions for a node."""
    rows = conn.execute(
        """
        SELECT rx_time, lat, lon, altitude_m
        FROM positions
        WHERE node_id = ?
        ORDER BY rx_time ASC, id ASC
        """,
        (node_id,),
    ).fetchall()
    if len(rows) < 2:
        return None

    points = [
        {
            "rx_time": int(r["rx_time"]),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "altitude_m": float(r["altitude_m"]) if r["altitude_m"] is not None else None,
        }
        for r in rows
        if r["lat"] is not None and r["lon"] is not None
    ]
    if len(points) < 2:
        return None

    snapped_points, snap_provider = _snap_points_to_road(points)

    now = int(time.time())
    conn.execute("UPDATE tracks SET is_selected = 0")
    cur = conn.execute(
        """
        INSERT INTO tracks (node_id, created_ts, start_ts, end_ts, point_count, color, is_selected, path_json, snapped_path_json, snap_provider)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            node_id,
            now,
            points[0]["rx_time"],
            points[-1]["rx_time"],
            len(points),
            color,
            json.dumps(points),
            json.dumps(snapped_points) if snapped_points else None,
            snap_provider,
        ),
    )
    conn.commit()
    track_id = cur.lastrowid
    return {
        "id": track_id,
        "node_id": node_id,
        "created_ts": now,
        "start_ts": points[0]["rx_time"],
        "end_ts": points[-1]["rx_time"],
        "point_count": len(points),
        "color": color,
        "is_selected": True,
        "points": snapped_points if snapped_points else points,
        "raw_points": points,
        "is_snapped": bool(snapped_points),
        "snap_provider": snap_provider,
    }


def list_tracks(conn, *, mode: str = "all", limit: int = 200) -> list[dict]:
    now = int(time.time())
    where_parts = []
    params: list = []
    if mode == "selected":
        where_parts.append("t.is_selected = 1")
    if mode == "in_motion":
        where_parts.append("n.last_motion_ts IS NOT NULL")
        where_parts.append("(? - n.last_motion_ts) < ?")
        params.extend([now, _VEHICLE_TIMEOUT_S])

    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    rows = conn.execute(
        f"""
        SELECT t.id, t.node_id, t.created_ts, t.start_ts, t.end_ts, t.point_count,
               t.color, t.is_selected, t.path_json, t.snapped_path_json, t.snap_provider,
               n.last_motion_ts,
               CASE
                 WHEN n.last_motion_ts IS NOT NULL AND (? - n.last_motion_ts) < ? THEN 1
                 ELSE 0
               END AS is_moving
        FROM tracks t
        LEFT JOIN nodes n ON n.node_id = t.node_id
        {where}
        ORDER BY t.created_ts DESC
        LIMIT ?
        """,
        tuple([now, _VEHICLE_TIMEOUT_S, *params, limit]),
    ).fetchall()

    out = []
    for row in rows:
        try:
            pts = json.loads(row["path_json"])
            if not isinstance(pts, list):
                pts = []
        except Exception:
            pts = []
        snapped_pts = []
        try:
            raw_snapped = row["snapped_path_json"]
            if raw_snapped:
                s = json.loads(raw_snapped)
                if isinstance(s, list):
                    snapped_pts = s
        except Exception:
            snapped_pts = []

        display_pts = snapped_pts if snapped_pts else pts

        out.append(
            {
                "id": row["id"],
                "node_id": row["node_id"],
                "created_ts": row["created_ts"],
                "start_ts": row["start_ts"],
                "end_ts": row["end_ts"],
                "point_count": row["point_count"],
                "color": row["color"] or "#ff9f1a",
                "is_selected": bool(row["is_selected"]),
                "points": display_pts,
                "raw_points": pts,
                "is_snapped": bool(snapped_pts),
                "snap_provider": row["snap_provider"],
                "is_moving": bool(row["is_moving"]),
            }
        )
    return out


def create_tracks_from_packets(conn, *, color: str = "#ff9f1a") -> dict:
    """Build selected tracks for every node with >=2 position points."""
    nodes = conn.execute(
        """
        SELECT node_id, COUNT(*) AS cnt
        FROM positions
        GROUP BY node_id
        HAVING COUNT(*) >= 2
        ORDER BY cnt DESC
        """
    ).fetchall()

    if not nodes:
        return {"created": 0, "node_count": 0}

    now = int(time.time())
    conn.execute("UPDATE tracks SET is_selected = 0")
    created = 0
    snapped = 0
    for row in nodes:
        node_id = row["node_id"]
        pts_rows = conn.execute(
            """
            SELECT rx_time, lat, lon, altitude_m
            FROM positions
            WHERE node_id = ?
            ORDER BY rx_time ASC, id ASC
            """,
            (node_id,),
        ).fetchall()
        points = [
            {
                "rx_time": int(p["rx_time"]),
                "lat": float(p["lat"]),
                "lon": float(p["lon"]),
                "altitude_m": float(p["altitude_m"]) if p["altitude_m"] is not None else None,
            }
            for p in pts_rows
            if p["lat"] is not None and p["lon"] is not None
        ]
        if len(points) < 2:
            continue

        snapped_points, snap_provider = _snap_points_to_road(points)

        conn.execute(
            """
            INSERT INTO tracks (node_id, created_ts, start_ts, end_ts, point_count, color, is_selected, path_json, snapped_path_json, snap_provider)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                node_id,
                now,
                points[0]["rx_time"],
                points[-1]["rx_time"],
                len(points),
                color,
                json.dumps(points),
                json.dumps(snapped_points) if snapped_points else None,
                snap_provider,
            ),
        )
        if snapped_points:
            snapped += 1
        created += 1

    conn.commit()
    return {"created": created, "node_count": len(nodes), "snapped": snapped}


def _snap_points_to_road(points: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Best-effort map-matching via OSRM; returns None when snapping is unavailable."""
    if len(points) < 2:
        return None, None

    pts = _downsample_points(points, _OSRM_MAX_POINTS)
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in pts)
    timestamps = ";".join(str(int(p["rx_time"])) for p in pts)
    query = urllib.parse.urlencode({
        "geometries": "geojson",
        "overview": "full",
        "tidy": "true",
        "timestamps": timestamps,
    })
    url = f"{_OSRM_MATCH_URL}{coords}?{query}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NMDisplay/1.0"})
        with urllib.request.urlopen(req, timeout=_OSRM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None, None

    if data.get("code") != "Ok":
        return None, None

    matchings = data.get("matchings") or []
    if not matchings:
        return None, None

    snapped_points = []
    for m in matchings:
        geom = m.get("geometry") or {}
        coords_arr = geom.get("coordinates") or []
        for c in coords_arr:
            if not isinstance(c, (list, tuple)) or len(c) < 2:
                continue
            snapped_points.append({"lat": float(c[1]), "lon": float(c[0]), "rx_time": None, "altitude_m": None})

    if len(snapped_points) < 2:
        return None, None

    # Preserve original start/end timestamps for labeling consistency.
    snapped_points[0]["rx_time"] = int(points[0]["rx_time"])
    snapped_points[-1]["rx_time"] = int(points[-1]["rx_time"])
    return snapped_points, "osrm"


def _downsample_points(points: list[dict], max_points: int) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = max(1, (len(points) - 1) // (max_points - 1))
    sampled = points[::step]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    if sampled[0] is not points[0]:
        sampled.insert(0, points[0])
    # Ensure hard cap while preserving ends.
    if len(sampled) > max_points:
        core = sampled[1:-1]
        keep = max_points - 2
        core_step = max(1, len(core) // keep)
        core = core[::core_step][:keep]
        sampled = [sampled[0], *core, sampled[-1]]
    return sampled
