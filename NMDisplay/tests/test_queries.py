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


if __name__ == "__main__":
    unittest.main()
