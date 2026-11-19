import ast
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


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
            ('g', 'epd2in13g_V2', 4),
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
        driver = types.ModuleType("epd2in13g_V2")
        driver.epdconfig = mock.Mock()
        driver.EPD = mock.Mock()
        driver.EPD.return_value.init.return_value = -1
        package.epd2in13g_V2 = driver
        with mock.patch.dict(sys.modules, {"waveshare_epd": package}):
            display = display_driver.init_display('g')
            with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
                display.init()


class GV2DriverTests(unittest.TestCase):
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
            "waveshare_epd.epd2in13g_V2", GAUGE_DIR / "waveshare_epd" / "epd2in13g_V2.py")
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

    def test_v2_initialization_uses_v2_panel_commands(self):
        self.config.module_init = mock.Mock(return_value=0)
        epd = self.driver.EPD()
        with mock.patch.object(epd, "reset"), mock.patch.object(epd, "ReadBusy"), \
                mock.patch.object(epd, "send_command") as command, \
                mock.patch.object(epd, "send_data") as data:
            self.assertEqual(epd.init(), 0)
        self.assertEqual(command.call_args_list, [mock.call(0x61), mock.call(0xE9), mock.call(0x04)])
        self.assertEqual(data.call_args_list,
                         [mock.call(x) for x in (0x00, 0x7C, 0x00, 0xFA, 0x01)])

    def test_v2_initializers_use_special_gpio_setup_when_available(self):
        self.config.module_init = mock.Mock()
        self.config.module_init_g_v2 = mock.Mock(return_value=-1)
        try:
            epd = self.driver.EPD()
            self.assertEqual(epd.init(), -1)
            self.assertEqual(epd.init_Fast(), -1)
            self.assertEqual(self.config.module_init_g_v2.call_count, 2)
            self.config.module_init.assert_not_called()
        finally:
            del self.config.module_init_g_v2

    def test_four_color_buffer_for_portrait_and_landscape(self):
        epd = self.driver.EPD()
        for size in ((122, 250), (250, 122)):
            for color, expected in (((0, 0, 0), 0x00), ((255, 255, 255), 0x55),
                                    ((255, 255, 0), 0xaa), ((255, 0, 0), 0xff)):
                with self.subTest(size=size, color=color):
                    buffer = epd.getbuffer(Image.new("RGB", size, color))
                    self.assertEqual(len(buffer), 31 * 250)
                    self.assertEqual(buffer[0], expected)
                    self.assertEqual(buffer[-1], expected & 0xf0)

    def test_color_refresh_uses_v2_full_display(self):
        epd = self.driver.EPD()
        buffer = [0x55] * (31 * 250)
        with mock.patch.object(epd, "send_command") as command, \
                mock.patch.object(epd, "send_data") as data, \
                mock.patch.object(epd, "TurnOnDisplay") as refresh:
            epd.display(buffer)
        command.assert_called_once_with(0x10)
        self.assertEqual(data.call_count, 31 * 250)
        refresh.assert_called_once_with()


class RaspberryPiWakeupTests(unittest.TestCase):
    def test_wakeup_only_for_g_v2(self):
        config_path = GAUGE_DIR / "waveshare_epd" / "epdconfig.py"
        tree = ast.parse(config_path.read_text())
        pi_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "RaspberryPi")
        namespace = {}
        exec(compile(ast.Module(body=[pi_class], type_ignores=[]), str(config_path), "exec"), namespace)
        pi = namespace["RaspberryPi"].__new__(namespace["RaspberryPi"])
        pi.GPIO_PWR_PIN = mock.Mock()
        pi.GPIO_RST_PIN = mock.Mock()
        pi.GPIO_DC_PIN = mock.Mock()
        original_busy = mock.Mock()
        pi.GPIO_BUSY_PIN = original_busy
        pi.SPI = mock.Mock()
        pi.delay_ms = mock.Mock()
        gpiozero = types.ModuleType("gpiozero")
        gpiozero.LED = mock.Mock()
        gpiozero.Button = mock.Mock()

        with mock.patch.dict(sys.modules, {"gpiozero": gpiozero}):
            self.assertEqual(pi.module_init(), 0)
            gpiozero.LED.assert_not_called()
            self.assertEqual(pi.module_init_g_v2(), 0)

        original_busy.close.assert_called_once_with()
        gpiozero.LED.assert_called_once_with(pi.BUSY_PIN)
        gpiozero.LED.return_value.on.assert_called_once_with()
        gpiozero.LED.return_value.close.assert_called_once_with()
        pi.delay_ms.assert_called_once_with(20)
        gpiozero.Button.assert_called_once_with(pi.BUSY_PIN, pull_up=False)
        self.assertIs(pi.GPIO_BUSY_PIN, gpiozero.Button.return_value)


if __name__ == "__main__":
    unittest.main()
