import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import queries


class PacketBreakdownTests(unittest.TestCase):
    def test_gauges_session_packets_excludes_reference_node_packets(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            now = 1_700_000_000

            conn.execute("CREATE TABLE nodes (node_id INTEGER, last_heard INTEGER, hop_limit INTEGER, role TEXT)")
            conn.execute(
                "CREATE TABLE packets (from_node_id INTEGER, hop_limit INTEGER, hop_start INTEGER, rx_time INTEGER, rx_rssi INTEGER, rx_snr REAL, portnum TEXT)"
            )
            conn.execute("CREATE TABLE packet_timeline (minute_ts INTEGER, count INTEGER, portnum TEXT)")

            conn.execute("INSERT INTO nodes VALUES (123456789, ?, 3, 'ROUTER')", (now,))
            conn.execute("INSERT INTO nodes VALUES (987654321, ?, 3, 'CLIENT')", (now,))

            # Reference node packets (should be excluded from session_packets gauge).
            conn.execute("INSERT INTO packets VALUES (123456789, 3, 3, ?, -60, 5.0, 'TELEMETRY_APP')", (now - 10,))
            conn.execute("INSERT INTO packets VALUES (123456789, 3, 3, ?, -60, 5.0, 'POSITION_APP')", (now - 9,))

            # Peer packet (should be counted).
            conn.execute("INSERT INTO packets VALUES (987654321, 2, 3, ?, -70, 4.0, 'TEXT_MESSAGE_APP')", (now - 8,))

            conn.execute("INSERT INTO packet_timeline VALUES (?, 3, 'TEXT_MESSAGE_APP')", (now - 60,))
            conn.commit()

            real_time = queries.time.time
            queries.time.time = lambda: now
            try:
                gauges = queries.get_gauges(conn, my_node_id=123456789, my_lat=None, my_lon=None)
            finally:
                queries.time.time = real_time
        finally:
            conn.close()

        self.assertEqual(gauges["session_packets"], 1)

    def test_gauges_hop_usage_percentage_buckets(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            now = 1_700_000_000
            conn.execute("CREATE TABLE nodes (node_id INTEGER, last_heard INTEGER, hop_limit INTEGER, role TEXT)")
            conn.execute(
                "CREATE TABLE packets (from_node_id INTEGER, hop_limit INTEGER, hop_start INTEGER, rx_time INTEGER, rx_rssi INTEGER, rx_snr REAL, portnum TEXT)"
            )
            conn.execute("CREATE TABLE packet_timeline (minute_ts INTEGER, count INTEGER, portnum TEXT)")

            conn.execute("INSERT INTO nodes VALUES (123456789, ?, 3, 'ROUTER')", (now,))
            conn.execute("INSERT INTO nodes VALUES (987654321, ?, 3, 'CLIENT')", (now,))

            # 66% usage => bucket 60
            conn.execute("INSERT INTO packets VALUES (987654321, 1, 3, ?, -70, 4.0, 'TEXT_MESSAGE_APP')", (now - 20,))
            # 25% usage => bucket 20
            conn.execute("INSERT INTO packets VALUES (987654321, 3, 4, ?, -70, 4.0, 'TEXT_MESSAGE_APP')", (now - 19,))
            # 100% usage => bucket 100
            conn.execute("INSERT INTO packets VALUES (987654321, 0, 3, ?, -70, 4.0, 'TEXT_MESSAGE_APP')", (now - 18,))

            conn.execute("INSERT INTO packet_timeline VALUES (?, 3, 'TEXT_MESSAGE_APP')", (now - 60,))
            conn.commit()

            real_time = queries.time.time
            queries.time.time = lambda: now
            try:
                gauges = queries.get_gauges(conn, my_node_id=123456789, my_lat=None, my_lon=None)
            finally:
                queries.time.time = real_time
        finally:
            conn.close()

        buckets = gauges["hop_usage_pct_buckets"]
        self.assertEqual(buckets["60"], 1)
        self.assertEqual(buckets["20"], 1)
        self.assertEqual(buckets["100"], 1)

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
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER, hop_start INTEGER)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT, packet_source TEXT)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (987654321, 'B', 'Beta', 'Over the Air')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'TELEMETRY_APP', -60, 3.0, 3, 3)")
            conn.execute("INSERT INTO packets VALUES (2, 101, 987654321, NULL, 'TELEMETRY_APP', -55, 4.0, 3, 3)")
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
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER, hop_start INTEGER)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT, hardware_model TEXT, role TEXT, packet_source TEXT)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Heltec V4', 'ROUTER', 'Over the Air')")
            conn.execute("INSERT INTO nodes VALUES (987654321, 'B', 'Beta', 'Heltec V4', 'ROUTER', 'MQTT')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'TELEMETRY_APP', -60, 3.0, 3, 3)")
            conn.execute("INSERT INTO packets VALUES (2, 101, 987654321, NULL, 'TEXT_MESSAGE_APP', -55, 4.0, 3, 3)")
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
                    voltage REAL, hop_limit INTEGER, packet_source TEXT, role TEXT, street TEXT, city TEXT, state TEXT
                )
                """
            )
            conn.execute("CREATE TABLE packets (from_node_id INTEGER, hop_limit INTEGER, hop_start INTEGER, rx_time INTEGER, portnum TEXT, id INTEGER)")
            conn.execute("CREATE TABLE message_counts (node_id INTEGER, sent_count INTEGER, received_count INTEGER, last_updated INTEGER)")
            conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, packet_id INTEGER, rx_time INTEGER, node_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Heltec V4', 1, 2, NULL, 0, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, NULL, NULL, NULL, NULL, NULL, 'MQTT', 'ROUTER', NULL, NULL, NULL)")
            conn.commit()

            rows = queries.get_all_nodes(conn, None, None)
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "LOC_INTERNAL")
        self.assertEqual(rows[0]["sats"], 7)
        self.assertEqual(rows[0]["packet_source"], "MQTT")

    def test_filtered_traffic_exposes_position_source_and_sats(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER, hop_start INTEGER)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT, packet_source TEXT)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'MQTT')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'POSITION_APP', -60, 3.0, 3, 3)")
            conn.execute("INSERT INTO positions VALUES (1, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, 13)")
            conn.commit()

            rows = queries.get_filtered_traffic(conn, 'portnum', 'POSITION_APP', limit=10)
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], 'LOC_INTERNAL')
        self.assertEqual(rows[0]['sats'], 7)
        self.assertEqual(rows[0]['packet_source'], 'MQTT')

    def test_packet_detail_exposes_position_source_and_sats(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, rx_rssi INTEGER, rx_snr REAL, hop_limit INTEGER, hop_start INTEGER, payload_json TEXT)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, short_name TEXT, long_name TEXT, packet_source TEXT)")
            conn.execute("CREATE TABLE telemetry (packet_id INTEGER, battery_level INTEGER, voltage REAL, channel_util REAL, air_util_tx REAL, temperature REAL, relative_humidity REAL, barometric_pressure REAL, gas_resistance REAL)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("CREATE TABLE text_messages (packet_id INTEGER, message_text TEXT, to_node_id INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Over the Air')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'POSITION_APP', -60, 3.0, 3, 3, '{\"decoded\":true}')")
            conn.execute("INSERT INTO positions VALUES (1, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, 13)")
            conn.commit()

            detail = queries.get_packet_detail(conn, 1)
        finally:
            conn.close()

        self.assertIsNotNone(detail)
        self.assertEqual(detail["position"]["source"], "LOC_INTERNAL")
        self.assertEqual(detail["position"]["sats"], 7)
        self.assertEqual(detail["packet_source"], "Over the Air")

    def test_packets_export_exposes_position_source_and_sats(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE packets (id INTEGER, rx_time INTEGER, from_node_id INTEGER, to_node_id INTEGER, portnum TEXT, hop_limit INTEGER, hop_start INTEGER, rx_rssi INTEGER, rx_snr REAL, rx_lat REAL, rx_lon REAL)")
            conn.execute("CREATE TABLE nodes (node_id INTEGER, packet_source TEXT)")
            conn.execute("CREATE TABLE positions (packet_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'MQTT')")
            conn.execute("INSERT INTO packets VALUES (1, 100, 123456789, NULL, 'POSITION_APP', 3, 3, -60, 3.0, 38.1, -76.9)")
            conn.execute("INSERT INTO positions VALUES (1, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, 13)")
            conn.commit()

            rows = queries.get_packets_for_export(conn)
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "LOC_INTERNAL")
        self.assertEqual(rows[0]["sats"], 7)
        self.assertEqual(rows[0]["packet_source"], "MQTT")

    def test_nodes_csv_includes_packet_source_column(self):
        import app as app_mod

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
                    voltage REAL, hop_limit INTEGER, packet_source TEXT, role TEXT, street TEXT, city TEXT, state TEXT
                )
                """
            )
            conn.execute("CREATE TABLE packets (from_node_id INTEGER, hop_limit INTEGER, hop_start INTEGER, rx_time INTEGER, portnum TEXT, id INTEGER)")
            conn.execute("CREATE TABLE message_counts (node_id INTEGER, sent_count INTEGER, received_count INTEGER, last_updated INTEGER)")
            conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, packet_id INTEGER, rx_time INTEGER, node_id INTEGER, lat REAL, lon REAL, altitude_m REAL, source TEXT, sats INTEGER, precision_bits INTEGER)")
            conn.execute("INSERT INTO nodes VALUES (123456789, 'A', 'Alpha', 'Heltec V4', 1, 2, NULL, 0, 38.1, -76.9, 10.0, 'LOC_INTERNAL', 7, NULL, NULL, NULL, NULL, NULL, 'MQTT', 'ROUTER', NULL, NULL, NULL)")
            conn.commit()

            csv_data = app_mod._build_nodes_csv(conn)
        finally:
            conn.close()

        header = csv_data.splitlines()[0]
        self.assertIn("Packet Source", header)
        self.assertIn("MQTT", csv_data)


if __name__ == "__main__":
    unittest.main()
