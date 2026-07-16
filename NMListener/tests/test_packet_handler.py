import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import packet_handler


class DummyRepo:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("CREATE TABLE packets (id INTEGER PRIMARY KEY)")
        self.packets = []
        self.telemetry = []
        self.positions = []

    def upsert_node(self, *args, **kwargs):
        self.nodes = getattr(self, "nodes", [])
        self.nodes.append({"args": args, "kwargs": kwargs})

    def insert_packet(self, *, rx_time, from_node_id, to_node_id, portnum, portnum_int,
                      hop_limit, hop_start, rx_rssi, rx_snr, rx_lat, rx_lon, payload_raw, payload_dict):
        cur = self._conn.execute("INSERT INTO packets DEFAULT VALUES")
        self._conn.commit()
        packet_id = cur.lastrowid
        self.packets.append({
            "id": packet_id,
            "rx_time": rx_time,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "portnum": portnum,
            "hop_start": hop_start,
            "payload_dict": payload_dict,
        })
        return packet_id

    def insert_telemetry(self, packet_id, *, rx_time, node_id, battery_level=None,
                         voltage=None, channel_util=None, air_util_tx=None,
                         temperature=None, relative_humidity=None,
                         barometric_pressure=None, gas_resistance=None):
        self.telemetry.append({
            "packet_id": packet_id,
            "rx_time": rx_time,
            "node_id": node_id,
            "battery_level": battery_level,
            "voltage": voltage,
            "channel_util": channel_util,
            "air_util_tx": air_util_tx,
            "temperature": temperature,
            "relative_humidity": relative_humidity,
            "barometric_pressure": barometric_pressure,
            "gas_resistance": gas_resistance,
        })

    def insert_position(self, packet_id, *, rx_time, node_id, lat, lon,
                        altitude_m=None, source=None, sats=None, precision_bits=None):
        self.positions.append({
            "packet_id": packet_id,
            "rx_time": rx_time,
            "node_id": node_id,
            "lat": lat,
            "lon": lon,
            "altitude_m": altitude_m,
            "source": source,
            "sats": sats,
            "precision_bits": precision_bits,
        })

    def increment_timeline(self, minute_ts, portnum):
        self.timeline = getattr(self, "timeline", [])
        self.timeline.append({"minute_ts": minute_ts, "portnum": portnum})

    def get_recent_positions_for_node(self, node_id, *, since_ts, limit=120):
        rows = [
            p for p in self.positions
            if p["node_id"] == node_id and p["rx_time"] >= since_ts
        ]
        rows.sort(key=lambda p: p["rx_time"])
        return [
            {
                "rx_time": p["rx_time"],
                "lat": p["lat"],
                "lon": p["lon"],
                "altitude_m": p.get("altitude_m"),
                "sats": p.get("sats"),
                "precision_bits": p.get("precision_bits"),
            }
            for p in rows[-limit:]
        ]

    def update_motion_state(self, node_id, *, motion_eval_ts, motion_score, last_motion_ts=None):
        self.motion = getattr(self, "motion", [])
        self.motion.append(
            {
                "node_id": node_id,
                "motion_eval_ts": motion_eval_ts,
                "motion_score": motion_score,
                "last_motion_ts": last_motion_ts,
            }
        )


class DummyNodeStore:
    def upsert(self, *args, **kwargs):
        pass


class DummyTimeline:
    def record(self, _portnum, rx_time):
        return (rx_time // 60) * 60


class PacketHandlerTests(unittest.TestCase):
    def setUp(self):
        packet_handler.set_my_node(123456789)

    def test_reference_telemetry_is_written_for_timeline(self):
        repo = DummyRepo()
        node_store = DummyNodeStore()
        packet = {
            "from": 123456789,
            "rxTime": 1700000000,
            "decoded": {
                "telemetry": {
                    "deviceMetrics": {
                        "batteryLevel": 4,
                        "voltage": 3.3,
                        "channelUtilization": 5.5,
                        "airUtilTx": 0.75,
                    },
                    "environmentMetrics": {
                        "temperature": 21.4,
                        "relativeHumidity": 44.1,
                        "barometricPressure": 1013.2,
                    },
                }
            },
        }

        packet_handler.handle_telemetry(packet, repo, node_store, None, None)

        self.assertEqual(len(repo.packets), 1)
        self.assertEqual(len(repo.telemetry), 1)
        self.assertEqual(repo.telemetry[0]["channel_util"], 5.5)
        self.assertEqual(repo.telemetry[0]["air_util_tx"], 0.75)

    def test_position_packet_persists_source_and_sats(self):
        repo = DummyRepo()
        repo._conn.execute("INSERT INTO packets (id) VALUES (1)")
        repo._conn.commit()
        node_store = DummyNodeStore()
        packet = {
            "from": 987654321,
            "rxTime": 1700000001,
            "decoded": {
                "position": {
                    "latitude": 37.1234567,
                    "longitude": -122.1234567,
                    "altitude": 12.5,
                    "precisionBits": 28,
                    "satsInView": 7,
                    "locationSource": "LOC_INTERNAL",
                }
            },
        }

        packet_handler.handle_position(packet, repo, node_store, None, None)

        self.assertEqual(len(repo.positions), 1)
        pos = repo.positions[0]
        self.assertEqual(pos["source"], "LOC_INTERNAL")
        self.assertEqual(pos["sats"], 7)
        self.assertAlmostEqual(pos["lat"], 37.1234567)
        self.assertAlmostEqual(pos["lon"], -122.1234567)

    def test_position_packet_persists_packet_source_from_rx_flags(self):
        repo = DummyRepo()
        repo._conn.execute("INSERT INTO packets (id) VALUES (1)")
        repo._conn.commit()
        node_store = DummyNodeStore()
        packet = {
            "from": 987654321,
            "rxTime": 1700000002,
            "rx_flags": 0x10,
            "decoded": {
                "position": {
                    "latitude": 37.1234567,
                    "longitude": -122.1234567,
                }
            },
        }

        packet_handler.handle_position(packet, repo, node_store, None, None)

        self.assertEqual(len(repo.nodes), 1)
        self.assertEqual(repo.nodes[0]["kwargs"]["packet_source"], "MQTT")

    def test_position_packet_defaults_packet_source_to_ota(self):
        repo = DummyRepo()
        repo._conn.execute("INSERT INTO packets (id) VALUES (1)")
        repo._conn.commit()
        node_store = DummyNodeStore()
        packet = {
            "from": 987654321,
            "rxTime": 1700000003,
            "decoded": {
                "position": {
                    "latitude": 37.1234567,
                    "longitude": -122.1234567,
                }
            },
        }

        packet_handler.handle_position(packet, repo, node_store, None, None)

        self.assertEqual(len(repo.nodes), 1)
        self.assertEqual(repo.nodes[0]["kwargs"]["packet_source"], "Over the Air")

    def test_motion_classifier_ignores_altitude_only_jitter(self):
        now = 1_700_000_000
        samples = [
            {"rx_time": now - 300, "lat": 38.100000, "lon": -76.900000, "altitude_m": 10.0, "sats": 8, "precision_bits": 12},
            {"rx_time": now - 240, "lat": 38.100000, "lon": -76.900000, "altitude_m": 40.0, "sats": 8, "precision_bits": 12},
            {"rx_time": now - 180, "lat": 38.100000, "lon": -76.900000, "altitude_m": 75.0, "sats": 8, "precision_bits": 12},
            {"rx_time": now - 120, "lat": 38.100000, "lon": -76.900000, "altitude_m": 5.0, "sats": 8, "precision_bits": 12},
            {"rx_time": now - 60,  "lat": 38.100000, "lon": -76.900000, "altitude_m": 60.0, "sats": 8, "precision_bits": 12},
        ]

        moving, score = packet_handler._is_moving_from_positions(samples, now)

        self.assertFalse(moving)
        self.assertLess(score, 4)

    def test_generic_telemetry_packet_persists_packet_source_mqtt(self):
        repo = DummyRepo()
        node_store = DummyNodeStore()
        timeline = DummyTimeline()

        packet = {
            "from": 987654321,
            "rxTime": 1700000010,
            "rx_flags": 0x10,
            "decoded": {
                "portnum": "TELEMETRY_APP",
            },
        }

        packet_handler.handle_generic(packet, None, repo, node_store, timeline, None, None)

        self.assertEqual(len(repo.nodes), 1)
        self.assertEqual(repo.nodes[0]["kwargs"]["packet_source"], "MQTT")

    def test_generic_private_channel_packet_persists_packet_source_ota(self):
        repo = DummyRepo()
        node_store = DummyNodeStore()
        timeline = DummyTimeline()

        packet = {
            "from": 987654321,
            "to": 4294967295,
            "rxTime": 1700000011,
            "decoded": {
                "portnum": "UNKNOWN",
            },
        }

        packet_handler.handle_generic(packet, None, repo, node_store, timeline, None, None)

        self.assertEqual(len(repo.nodes), 1)
        self.assertEqual(repo.nodes[0]["kwargs"]["packet_source"], "Over the Air")
        self.assertEqual(len(repo.packets), 1)
        self.assertEqual(repo.packets[0]["portnum"], "UNKNOWN_PRIVATE_CH")


if __name__ == "__main__":
    unittest.main()
