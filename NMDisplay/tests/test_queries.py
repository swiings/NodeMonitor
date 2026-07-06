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


if __name__ == "__main__":
    unittest.main()
