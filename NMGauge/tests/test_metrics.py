import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageFont


GAUGE_PATH = Path(__file__).resolve().parents[1] / "nmEGaugeUpdate.py"
NOW = 1_700_000_000


def load_gauge():
    spec = importlib.util.spec_from_file_location("nmEGaugeUpdate", GAUGE_PATH)
    gauge = importlib.util.module_from_spec(spec)
    waveshare = types.ModuleType("waveshare_epd")
    waveshare.epd2in13_V4 = None
    font = ImageFont.load_default()
    background = Image.new("1", (250, 122), 1)
    with mock.patch.dict(sys.modules, {"waveshare_epd": waveshare}), \
            mock.patch.object(ImageFont, "truetype", return_value=font), \
            mock.patch.object(Image, "open", return_value=background):
        spec.loader.exec_module(gauge)
    return gauge


gauge = load_gauge()


class GaugeMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        data_dir = Path(self.temp_dir.name)
        (data_dir / "active.json").write_text(json.dumps({
            "db": "session.db",
            "created": NOW - 90_061,
        }))
        (data_dir / "listener_runtime.json").write_text(json.dumps({
            "my_node_id": 1,
            "channel_num": 0,
            "frequency_mhz": 906.875,
        }))
        data_dir_patch = mock.patch.object(gauge, "NMDATA", str(data_dir))
        data_dir_patch.start()
        self.addCleanup(data_dir_patch.stop)

        self.conn = sqlite3.connect(data_dir / "session.db")
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE nodes (
                node_id INTEGER PRIMARY KEY,
                short_name TEXT,
                long_name TEXT,
                lat REAL,
                lon REAL,
                last_heard INTEGER,
                rssi INTEGER,
                snr REAL
            );
            CREATE TABLE packets (
                from_node_id INTEGER,
                rx_rssi INTEGER,
                rx_time INTEGER,
                hop_start INTEGER,
                hop_limit INTEGER
            );
        """)

    def add_node(self, node_id, short_name, *, long_name=None, lat=None, lon=None,
                 last_heard=NOW - 100, rssi=None, snr=None):
        self.conn.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, short_name, long_name, lat, lon, last_heard, rssi, snr),
        )

    def add_packets(self, node_id, count, rssi=-70):
        self.conn.executemany(
            "INSERT INTO packets VALUES (?, ?, ?, 3, 3)",
            [(node_id, rssi, NOW - 30)] * count,
        )

    def get_metrics(self):
        self.conn.commit()
        with mock.patch.object(gauge.time, "time", return_value=NOW), \
                mock.patch.object(gauge, "battery_str", return_value="87%"), \
                mock.patch.object(gauge, "storage_str", return_value="42%"):
            return gauge.get_metrics()

    def test_metrics_pair_each_value_with_the_same_node(self):
        self.add_node(1, "REF", long_name="Reference", lat=40, lon=-75,
                      last_heard=NOW, rssi=-10, snr=9.0)
        self.add_node(2, "NEAR", lat=40.1, lon=-75, last_heard=NOW - 50, rssi=-80)
        self.add_node(3, "FAR", lat=41, lon=-75, last_heard=NOW - 10, rssi=-45, snr=4.5)
        self.add_node(4, "LOUD", rssi=-30, snr=3.0)
        self.add_node(5, "TALK", last_heard=NOW - 120, rssi=-70)
        self.add_packets(5, 3)
        self.add_packets(3, 1)
        self.add_packets(1, 10, rssi=None)

        metrics = self.get_metrics()
        expected = {
            "total": "5", "act": "5", "dir": "2",
            "packets": "4", "permin": "0.1", "perhour": "4",
            "ref_name": "REFERENCE", "uptime": "1d 1h 1m",
            "closest_node": "NEAR", "closest": "6.9m",
            "farthest_node": "FAR", "farthest": "69.1m",
            "loudest_node": "LOUD", "loudest": "-30 3.0",
            "last_heard_node": "FAR", "last_heard": "0m 10s",
            "noisiest_node": "TALK", "noisiest": "3",
        }
        self.assertEqual({key: metrics[key] for key in expected}, expected)

    def test_unnamed_nodes_use_hex_ids_for_each_metric(self):
        self.add_node(1, "REF", lat=0, lon=0, last_heard=NOW)
        self.add_node(-2, None, lat=0.1, lon=0, last_heard=NOW - 5, rssi=-20)
        self.add_node(3, "", lat=1, lon=0, last_heard=NOW - 300, rssi=-60)
        self.add_packets(-2, 3)
        self.add_packets(3, 1)

        metrics = self.get_metrics()
        expected = {
            "closest_node": "!fffffffe", "closest": "6.9m",
            "farthest_node": "!00000003", "farthest": "69.1m",
            "loudest_node": "!fffffffe", "loudest": "-20",
            "last_heard_node": "!fffffffe", "last_heard": "0m 5s",
            "noisiest_node": "!fffffffe", "noisiest": "3",
        }
        self.assertEqual({key: metrics[key] for key in expected}, expected)
        for page in (gauge.draw_metrics, gauge.draw_page2, gauge.draw_page3):
            with self.subTest(page=page.__name__):
                self.assertEqual(gauge.render(metrics, page).size, (122, 250))

    def test_node_values_can_be_concatenated_into_page_text(self):
        self.add_node(1, "REF", last_heard=NOW)
        self.add_node(42, None, last_heard=NOW - 10, rssi=-30, snr=2.0)
        self.add_packets(42, 2)
        metrics = self.get_metrics()

        for label, node_key, value_key, expected in (
            ("LATEST", "last_heard_node", "last_heard", "LATEST !0000002a 0m 10s"),
            ("LOUDEST", "loudest_node", "loudest", "LOUDEST !0000002a -30 2.0"),
            ("NOISIEST", "noisiest_node", "noisiest", "NOISIEST !0000002a 2"),
        ):
            with self.subTest(label=label):
                text = label + " " + metrics[node_key] + " " + metrics[value_key]
                self.assertEqual(text, expected)

    def test_opening_sequence_then_cycle_repeats_and_refreshes_metrics(self):
        self.add_node(1, "REF", last_heard=NOW)
        self.add_node(42, None, last_heard=NOW - 10, rssi=-30, snr=2.0)
        self.add_packets(42, 2)
        metrics = self.get_metrics()
        updated_metrics = dict(metrics, noisiest="3")
        background = Image.new("1", (250, 122), 1)
        fixed_now = gauge.datetime(2025, 1, 1, 12)
        next_clean = gauge.datetime(2025, 1, 2, 3)

        for cleanup_due in (False, True):
            with self.subTest(cleanup_due=cleanup_due):
                driver = mock.Mock()
                driver.getbuffer.side_effect = lambda image: image
                with mock.patch.object(gauge, "epd2in13_V4") as epd, \
                        mock.patch.object(gauge.Image, "open", return_value=background) as open_image, \
                        mock.patch.object(gauge.signal, "signal"), \
                        mock.patch.object(gauge.time, "sleep",
                                          side_effect=[None, None, None, None, None, StopIteration]) as sleep, \
                        mock.patch.object(gauge.time, "time", side_effect=[58, 59, 60, 61]), \
                        mock.patch.object(gauge, "datetime") as clock, \
                        mock.patch.object(gauge, "get_metrics",
                                          side_effect=[metrics, updated_metrics]) as get_metrics, \
                        mock.patch.object(gauge, "render", wraps=gauge.render) as render:
                    epd.EPD.return_value = driver
                    clock.now.side_effect = [fixed_now] + [
                        next_clean if cleanup_due else fixed_now
                    ] * 3
                    with self.assertRaises(StopIteration):
                        gauge.main()

                self.assertEqual(open_image.call_args_list, [
                    mock.call("haxxlabsooo1.png"),
                    mock.call("haxxlabsooo2.png"),
                    mock.call("haxxlabsooo3.png"),
                    mock.call("allwhitebackground.png"),
                    mock.call("eatsleep2.png"),
                ])
                self.assertEqual(sleep.call_args_list,
                                 [mock.call(2)] * 2 + [mock.call(gauge.PAGE_SECS)] * 4)
                self.assertEqual(render.call_args_list, [
                    mock.call(metrics, gauge.draw_metrics),
                    mock.call(metrics, gauge.draw_page2),
                    mock.call(updated_metrics, gauge.draw_page3),
                    mock.call(updated_metrics, gauge.draw_page4),
                ])
                self.assertEqual(get_metrics.call_count, 2)
                self.assertEqual(driver.init.call_count, 2 if cleanup_due else 1)
                self.assertEqual(driver.displayPartBaseImage.call_count, 4 if cleanup_due else 3)
                self.assertEqual(driver.displayPartial.call_count, 5 if cleanup_due else 6)

    def test_sender_missing_from_nodes_uses_hex_id(self):
        self.add_node(1, "REF", last_heard=NOW)
        self.add_packets(42, 2)

        metrics = self.get_metrics()
        self.assertEqual(metrics["noisiest_node"], "!0000002a")
        self.assertEqual(metrics["noisiest"], "2")

    def test_empty_session_keeps_placeholder_values_and_renders(self):
        metrics = self.get_metrics()
        for key in ("closest", "closest_node", "farthest", "farthest_node",
                    "loudest", "loudest_node", "last_heard", "last_heard_node",
                    "noisiest", "noisiest_node"):
            with self.subTest(key=key):
                self.assertEqual(metrics[key], "--")
        for page in (gauge.draw_metrics, gauge.draw_page2, gauge.draw_page3):
            with self.subTest(page=page.__name__):
                self.assertEqual(gauge.render(metrics, page).size, (122, 250))


class GaugePageTests(unittest.TestCase):
    def test_page_two_displays_all_node_metric_rows(self):
        metrics = {
            "datetime": "10/30 12:00",
            "total": "5", "act": "4", "dir": "2",
            "packets": "100", "permin": "1.5", "perhour": "100",
            "closest_node": "NEAR", "closest": "6.9m",
            "farthest_node": "FAR", "farthest": "69.1m",
            "loudest_node": "LOUD", "loudest": "-30 3.0",
            "last_heard_node": "LAST", "last_heard": "0m 10s",
            "noisiest_node": "TALK", "noisiest": "30",
        }
        draw = mock.Mock()
        gauge.draw_page2(draw, metrics)
        drawn = [c.args[1] for c in draw.text.call_args_list]
        for label, node, value in (
            ("CLOSEST", "NEAR", "6.9m"),
            ("FARTHEST", "FAR", "69.1m"),
            ("LOUDEST", "LOUD", "-30 3.0"),
            ("LATEST", "LAST", "0m 10s"),
            ("NOISIEST", "TALK", "30"),
        ):
            with self.subTest(label=label):
                for text in (label, node, value):
                    self.assertTrue(any(text in d for d in drawn), f"{text!r} not drawn")


class GaugeSettingsTests(unittest.TestCase):
    NAMES = ("UPDATE_SECS", "PAGE_SECS", "CLEAN_HOUR", "SCREEN_ROTATION")

    def setUp(self):
        saved = {name: getattr(gauge, name) for name in self.NAMES}
        self.addCleanup(lambda: [setattr(gauge, n, v) for n, v in saved.items()])
        self.defaults = saved
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.path = Path(temp_dir.name) / "NMGsettings.json"

    def load(self, content):
        self.path.write_text(content)
        with self.assertLogs(level="WARNING") as logs:
            gauge.load_settings(str(self.path))
            gauge.logging.warning("end")
        return [r for r in logs.output if not r.endswith(":end")]

    def current(self):
        return {name: getattr(gauge, name) for name in self.NAMES}

    def test_missing_file_keeps_defaults(self):
        gauge.load_settings(str(self.path))
        self.assertEqual(self.current(), self.defaults)

    def test_valid_settings_override_defaults(self):
        warnings = self.load(json.dumps({
            "update_secs": 30, "page_secs": 5, "clean_hour": 0, "screen_rotation": 90,
        }))
        self.assertEqual(warnings, [])
        self.assertEqual(self.current(), {
            "UPDATE_SECS": 30, "PAGE_SECS": 5, "CLEAN_HOUR": 0, "SCREEN_ROTATION": 90,
        })

    def test_partial_settings_override_only_given_keys(self):
        self.load(json.dumps({"screen_rotation": 90}))
        self.assertEqual(self.current(), dict(self.defaults, SCREEN_ROTATION=90))

    def test_invalid_and_unknown_values_are_ignored_with_warnings(self):
        warnings = self.load(json.dumps({
            "screen_rotation": 45, "page_secs": 0, "clean_hour": 24,
            "update_secs": "60", "rotation": 90,
        }))
        self.assertEqual(self.current(), self.defaults)
        self.assertEqual(len(warnings), 5)

    def test_malformed_file_is_ignored_with_warning(self):
        for content in ("{not json", "[90]"):
            with self.subTest(content=content):
                warnings = self.load(content)
                self.assertEqual(self.current(), self.defaults)
                self.assertEqual(len(warnings), 1)

    def test_rotation_setting_is_used_when_rendering(self):
        metrics = dict.fromkeys(
            ("datetime", "total", "act", "dir", "packets", "permin", "perhour", "uptime",
             "ref_name", "ref_ch", "freq", "battery", "storage", "closest", "loudest",
             "last_heard", "noisiest"), "--")
        upright = gauge.render(metrics)
        self.load(json.dumps({"screen_rotation": 90 if gauge.SCREEN_ROTATION == 270 else 270}))
        flipped = gauge.render(metrics)
        self.assertEqual(flipped.size, upright.size)
        self.assertEqual(flipped.tobytes(), upright.rotate(180).tobytes())


if __name__ == "__main__":
    unittest.main()
