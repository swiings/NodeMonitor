import json
import sqlite3
import time
from typing import Optional


class _BytesEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            return obj.hex()
        # Protobuf enums, message objects, and other non-standard types
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


class Repository:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def upsert_node(
        self,
        node_id: int,
        *,
        short_name: Optional[str] = None,
        long_name: Optional[str] = None,
        hardware_model: Optional[str] = None,
        last_heard: Optional[int] = None,
        device_last_heard: Optional[int] = None,
        seeded: int = 0,
        role: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        altitude_m: Optional[float] = None,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
        battery_level: Optional[int] = None,
        voltage: Optional[float] = None,
        hop_limit: Optional[int] = None,
    ) -> None:
        now = last_heard or int(time.time())
        self._conn.execute(
            """
            INSERT INTO nodes
                (node_id, short_name, long_name, hardware_model,
                 first_seen, last_heard, device_last_heard, seeded, role,
                 lat, lon, altitude_m, rssi, snr,
                 battery_level, voltage, hop_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                short_name        = COALESCE(excluded.short_name,        short_name),
                long_name         = COALESCE(excluded.long_name,         long_name),
                hardware_model    = COALESCE(excluded.hardware_model,    hardware_model),
                last_heard        = excluded.last_heard,
                device_last_heard = COALESCE(excluded.device_last_heard, device_last_heard),
                seeded            = MAX(seeded, excluded.seeded),
                role              = COALESCE(excluded.role,              role),
                lat               = COALESCE(excluded.lat,               lat),
                lon               = COALESCE(excluded.lon,               lon),
                altitude_m        = COALESCE(excluded.altitude_m,        altitude_m),
                rssi              = COALESCE(excluded.rssi,              rssi),
                snr               = COALESCE(excluded.snr,               snr),
                battery_level     = COALESCE(excluded.battery_level,     battery_level),
                voltage           = COALESCE(excluded.voltage,           voltage),
                hop_limit         = COALESCE(excluded.hop_limit,         hop_limit)
            """,
            (
                node_id, short_name, long_name, hardware_model,
                now, now, device_last_heard, seeded, role,
                lat, lon, altitude_m, rssi, snr,
                battery_level, voltage, hop_limit,
            ),
        )
        self._conn.commit()

    def get_all_nodes(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM nodes ORDER BY last_heard DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Packets
    # ------------------------------------------------------------------

    def insert_packet(
        self,
        *,
        rx_time: int,
        from_node_id: int,
        to_node_id: Optional[int],
        portnum: str,
        portnum_int: Optional[int],
        hop_limit: Optional[int],
        rx_rssi: Optional[int],
        rx_snr: Optional[float],
        rx_lat: Optional[float],
        rx_lon: Optional[float],
        payload_raw: Optional[bytes],
        payload_dict: Optional[dict],
    ) -> int:
        payload_json = json.dumps(payload_dict, cls=_BytesEncoder) if payload_dict is not None else None
        cur = self._conn.execute(
            """
            INSERT INTO packets
                (rx_time, from_node_id, to_node_id, portnum, portnum_int,
                 hop_limit, rx_rssi, rx_snr, rx_lat, rx_lon,
                 payload_raw, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rx_time, from_node_id, to_node_id, portnum, portnum_int,
                hop_limit, rx_rssi, rx_snr, rx_lat, rx_lon,
                payload_raw, payload_json,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------------------
    # Per-portnum child tables
    # ------------------------------------------------------------------

    def insert_text_message(
        self,
        packet_id: int,
        *,
        rx_time: int,
        from_node_id: int,
        to_node_id: Optional[int],
        message_text: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO text_messages
                (packet_id, rx_time, from_node_id, to_node_id, message_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (packet_id, rx_time, from_node_id, to_node_id, message_text),
        )
        self._conn.commit()

    def insert_telemetry(
        self,
        packet_id: int,
        *,
        rx_time: int,
        node_id: int,
        battery_level: Optional[int] = None,
        voltage: Optional[float] = None,
        channel_util: Optional[float] = None,
        air_util_tx: Optional[float] = None,
        temperature: Optional[float] = None,
        relative_humidity: Optional[float] = None,
        barometric_pressure: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO telemetry
                (packet_id, rx_time, node_id,
                 battery_level, voltage, channel_util, air_util_tx,
                 temperature, relative_humidity, barometric_pressure)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                packet_id, rx_time, node_id,
                battery_level, voltage, channel_util, air_util_tx,
                temperature, relative_humidity, barometric_pressure,
            ),
        )
        self._conn.commit()

    def insert_position(
        self,
        packet_id: int,
        *,
        rx_time: int,
        node_id: int,
        lat: float,
        lon: float,
        altitude_m: Optional[float] = None,
        precision_bits: Optional[int] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO positions
                (packet_id, rx_time, node_id, lat, lon, altitude_m, precision_bits)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (packet_id, rx_time, node_id, lat, lon, altitude_m, precision_bits),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def increment_timeline(self, minute_ts: int, portnum: str) -> None:
        self._conn.execute(
            """
            INSERT INTO packet_timeline (minute_ts, portnum, count) VALUES (?, ?, 1)
            ON CONFLICT(minute_ts, portnum) DO UPDATE SET count = count + 1
            """,
            (minute_ts, portnum),
        )
        self._conn.commit()

    def get_timeline(self, since_ts: int) -> list[dict]:
        cur = self._conn.execute(
            "SELECT minute_ts, portnum, count FROM packet_timeline WHERE minute_ts >= ? ORDER BY minute_ts",
            (since_ts,),
        )
        return [{"minute_ts": r[0], "portnum": r[1], "count": r[2]} for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Message counts
    # ------------------------------------------------------------------

    def upsert_message_count(
        self, node_id: int, *, sent_delta: int = 0, received_delta: int = 0
    ) -> None:
        now = int(time.time())
        self._conn.execute(
            """
            INSERT INTO message_counts (node_id, sent_count, received_count, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                sent_count     = sent_count     + excluded.sent_count,
                received_count = received_count + excluded.received_count,
                last_updated   = excluded.last_updated
            """,
            (node_id, sent_delta, received_delta, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
