import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


GAUGE_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("display_driver", GAUGE_DIR / "display_driver.py")
display_driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display_driver)


class DisplaySelectionTests(unittest.TestCase):
    def tearDown(self):
        display_driver.configure('v4')

    def test_explicit_drivers_and_color_capabilities(self):
        for kind, module_name, colors in (
            ('v4', 'epd2in13_V4', 2),
            ('g', 'epd2in13g', 4),
        ):
            with self.subTest(kind=kind):
                package = types.ModuleType("waveshare_epd")
                driver = types.ModuleType(module_name)
                driver.epdconfig = mock.Mock()
                driver.EPD = mock.Mock()
                driver.EPD.return_value.init.return_value = 0
                setattr(package, module_name, driver)
                with mock.patch.dict(sys.modules, {"waveshare_epd": package}), \
                        self.assertLogs(level="INFO") as logs:
                    display = display_driver.init_display(kind)
                    display.init()
                self.assertEqual(display.colors, colors)
                self.assertEqual(display_driver.DISPLAY_COLORS, colors)
                self.assertEqual(display_driver.IS_COLOR, colors == 4)
                self.assertIn(f"Selected display: {module_name}", logs.output[0])
                self.assertIn("initialized", logs.output[-1])

    def test_auto_and_unsupported_panel_fail_before_accessing_hardware(self):
        for kind, expected in (('auto', 'no longer supported'),
                               ('b', 'identify the B panel version'),
                               ('bad', 'Invalid display_type'),
                               ([], 'Invalid display_type')):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, expected):
                    display_driver.init_display(kind)

    def test_settings_are_read_without_probing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(display_driver.settings_display_type(path), 'v4')
            path.write_text(json.dumps({'display_type': 'g'}))
            self.assertEqual(display_driver.settings_display_type(path), 'g')
            path.write_text('{broken')
            with self.assertRaisesRegex(ValueError, 'Cannot read display settings'):
                display_driver.settings_display_type(path)

    def test_failed_initialization_is_not_reported_as_success(self):
        package = types.ModuleType("waveshare_epd")
        driver = types.ModuleType("epd2in13g")
        driver.epdconfig = mock.Mock()
        driver.EPD = mock.Mock()
        driver.EPD.return_value.init.return_value = -1
        package.epd2in13g = driver
        with mock.patch.dict(sys.modules, {"waveshare_epd": package}):
            display = display_driver.init_display('g')
            with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
                display.init()


class GBusyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = types.ModuleType("waveshare_epd")
        package.__path__ = [str(GAUGE_DIR / "waveshare_epd")]
        config = types.ModuleType("waveshare_epd.epdconfig")
        config.RST_PIN, config.DC_PIN, config.BUSY_PIN, config.CS_PIN = 17, 25, 24, 8
        config.delay_ms = mock.Mock()
        config.digital_read = mock.Mock()
        package.epdconfig = config
        spec = importlib.util.spec_from_file_location(
            "waveshare_epd.epd2in13g", GAUGE_DIR / "waveshare_epd" / "epd2in13g.py")
        cls.driver = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"waveshare_epd": package,
                                          "waveshare_epd.epdconfig": config}):
            spec.loader.exec_module(cls.driver)
        cls.config = config

    def test_busy_releases(self):
        self.config.digital_read.side_effect = [0, 1]
        with mock.patch.object(self.driver.time, "monotonic", side_effect=[0, 1]):
            self.driver.EPD().ReadBusy()

    def test_busy_stuck_low_times_out(self):
        self.config.digital_read.side_effect = None
        self.config.digital_read.return_value = 0
        with mock.patch.object(self.driver.time, "monotonic", side_effect=[0, 120]):
            with self.assertRaisesRegex(TimeoutError, 'BUSY stayed low'):
                self.driver.EPD().ReadBusy()


if __name__ == "__main__":
    unittest.main()
