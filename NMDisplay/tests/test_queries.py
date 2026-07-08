import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import queries


class PacketBreakdownTests(unittest.TestCase):
    def test_self_telemetry_is_excluded_from_breakdown(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (portnum TEXT, from_node_id INTEGER, to_node_id INTEGER)")
            conn.execute("INSERT INTO packets VALUES ('TELEMETRY_APP', 123456789, NULL)")
            conn.execute("INSERT INTO packets VALUES ('TEXT_MESSAGE_APP', 987654321, NULL)")
            conn.commit()

            rows = queries.get_packet_breakdown(conn, my_node_id=123456789)
        finally:
            conn.close()

        self.assertEqual(rows, [{"portnum": "TEXT_MESSAGE_APP", "count": 1}])

    def test_self_telemetry_is_excluded_from_traffic_detail(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT)")
            conn.execute("INSERT INTO nodes VALUES (987654321, 'B', 'Beta')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'TELEMETRY_APP', -60, 3.0, 3)")
            conn.execute("INSERT INTO packets VALUES (2, 101, 987654321, NULL, 'TELEMETRY_APP', -55, 4.0, 3)")
            conn.commit()

            rows = queries.get_filtered_traffic(conn, "portnum", "TELEMETRY_APP", limit=10, my_node_id=123456789)
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["from_node_id"], 987654321)

    def test_self_telemetry_is_excluded_from_hardware_and_role_views(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT, hardware_model TEXT, role TEXT)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Heltec V4', 'ROUTER')")
            conn.execute("INSERT INTO nodes VALUES (987654321, 'B', 'Beta', 'Heltec V4', 'ROUTER')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'TELEMETRY_APP', -60, 3.0, 3)")
            conn.execute("INSERT INTO packets VALUES (2, 101, 987654321, NULL, 'TEXT_MESSAGE_APP', -55, 4.0, 3)")
            conn.commit()

            hw_rows = queries.get_filtered_traffic(conn, "hardware", "Heltec V4", limit=10, my_node_id=123456789)
            role_rows = queries.get_filtered_traffic(conn, "role", "ROUTER", limit=10, my_node_id=123456789)
        finally:
            conn.close()

        self.assertEqual(len(hw_rows), 1)
        self.assertEqual(hw_rows[0]["from_node_id"], 987654321)
        self.assertEqual(len(role_rows), 1)
        self.assertEqual(role_rows[0]["from_node_id"], 987654321)

    def test_node_list_exposes_position_source_and_sats(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.create_function("haversine", 4, lambda *args: None)
            conn.execute(
                """
                CREATE TABLE nodes (
                    node_id INTEGER, short_name TEXT, long_name TEXT, hardware_model TEXT,
                    first_seen INTEGER, last_heard INTEGER, device_last_heard INTEGER,
                    seeded INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT,
                    sats INTEGER, rssi INTEGER, snr REAL, battery_level INTEGER,
                    voltage REAL, hop_limit INTEGER, role TEXT, street TEXT, city TEXT, state TEXT
                )
                """
            )
            conn.execute("CREATE TABLE packets (from_node_id INTEGER, hop_limit INTEGER, rx_time INTEGER, portnum TEXT)")
            conn.execute("CREATE TABLE message_counts (node_id INTEGER, sent_count INTEGER, received_count INTEGER, last_updated INTEGER)")
            conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, packet_id INTEGER, rx_time INTEGER, node_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Heltec V4', 1, 2, NULL, 0, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, NULL, NULL, NULL, NULL, NULL, 'ROUTER', NULL, NULL, NULL)")
            conn.commit()

            rows = queries.get_all_nodes(conn, None, None)
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "LOC_INTERNAL")
        self.assertEqual(rows[0]["sats"], 7)

    def test_packet_detail_exposes_position_source_and_sats(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER, payload_json TEXT)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT)")
            conn.execute("CREATE TABLE telemetry (packet_id INTEGER, battery_level INTEGER, voltage REAL, channel_util REAL, air_util_tx REAL, temperature REAL, relative_humidity REAL, barometric_pressure REAL, gas_resistance REAL)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("CREATE TABLE text_messages (packet_id INTEGER, message_text TEXT, to_node_id INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'POSITION_APP', -60, 3.0, 3, '{\"decoded\":true}')")
            conn.execute("INSERT INTO positions VALUES (1, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, 13)")
            conn.commit()

            detail = queries.get_packet_detail(conn, 1)
        finally:
            conn.close()

        self.assertIsNotNone(detail)
        self.assertEqual(detail["position"]["source"], "LOC_INTERNAL")
        self.assertEqual(detail["position"]["sats"], 7)


if __name__ == "__main__":
    unittest.main()
