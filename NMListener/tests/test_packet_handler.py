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

    def upsert_node(self, *args, **kwargs):
        pass

    def insert_packet(self, *, rx_time, from_node_id, to_node_id, portnum, portnum_int,
                      hop_limit, rx_rssi, rx_snr, rx_lat, rx_lon, payload_raw, payload_dict):
        cur = self._conn.execute("INSERT INTO packets DEFAULT VALUES")
        self._conn.commit()
        packet_id = cur.lastrowid
        self.packets.append({
            "id": packet_id,
            "rx_time": rx_time,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "portnum": portnum,
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


class DummyNodeStore:
    def upsert(self, *args, **kwargs):
        pass


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


if __name__ == "__main__":
    unittest.main()
