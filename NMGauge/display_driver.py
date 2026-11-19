#!/usr/bin/env python3
"""Driver selection for the Waveshare 2.13-inch e-Paper HAT.

Supports the 2-color V4 panel (epd2in13_V4) and the 4-color G V2 panel
(epd2in13g_V2), selected in NMGsettings.json. After init_display()
(or configure()) the module globals describe the active panel:

    DISPLAY_TYPE    'epd2in13_V4' (2-color) or 'epd2in13g_V2' (4-color)
    DISPLAY_COLORS  2 or 4
    IS_COLOR        True on the 4-color panel
    IMAGE_MODE      PIL mode to draw frames in: '1' (mono) or 'RGB' (color)
    BLACK / WHITE / RED / YELLOW
                    fill values valid for IMAGE_MODE; RED and YELLOW fall
                    back to BLACK on the 2-color panel so the same drawing
                    code renders sensibly on both

Set "display_type": "v4" or "g" in NMGsettings.json. BUSY polarity is not
a reliable way to identify a panel; it can also reflect a wiring fault.
"""

import json
import logging
import os

WIDTH, HEIGHT = 250, 122

# Globals describing the active panel; defaults match the 2-color V4 so
# drawing code works off-device (tests, previews) without any hardware.
DISPLAY_TYPE = 'epd2in13_V4'
DISPLAY_COLORS = 2
IS_COLOR = False
IMAGE_MODE = '1'
BLACK, WHITE, RED, YELLOW = 0, 255, 0, 0

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'NMGsettings.json')
_KINDS = {'v4': 'epd2in13_V4', 'epd2in13_V4': 'epd2in13_V4',
          'g': 'epd2in13g_V2', 'epd2in13g_V2': 'epd2in13g_V2'}


def configure(kind):
    """Set the module globals for the given driver kind ('v4' or 'g')."""
    global DISPLAY_TYPE, DISPLAY_COLORS, IS_COLOR, IMAGE_MODE
    global BLACK, WHITE, RED, YELLOW
    DISPLAY_TYPE = _KINDS[kind]
    IS_COLOR = DISPLAY_TYPE == 'epd2in13g_V2'
    DISPLAY_COLORS = 4 if IS_COLOR else 2
    IMAGE_MODE = 'RGB' if IS_COLOR else '1'
    if IS_COLOR:
        BLACK, WHITE = (0, 0, 0), (255, 255, 255)
        RED, YELLOW = (255, 0, 0), (255, 255, 0)
    else:
        BLACK, WHITE = 0, 255
        RED = YELLOW = BLACK    # color accents render as black on the mono panel


def settings_display_type(path=SETTINGS_FILE):
    """Read the selected panel, defaulting to V4 if the setting is absent."""
    try:
        with open(path) as f:
            settings = json.load(f)
    except FileNotFoundError:
        return 'v4'
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read display settings from {path}: {error}") from error
    if not isinstance(settings, dict):
        raise ValueError(f"Display settings in {path} must be a JSON object")
    return settings.get('display_type', 'v4')


class Display:
    """Wrapper exposing the same surface as the raw Waveshare drivers.

    On the 4-color panel, which has no partial-refresh support, the
    partial-update methods fall back to full (flashing) refreshes.
    """

    def __init__(self, kind):
        configure(kind)
        if IS_COLOR:
            from waveshare_epd import epd2in13g_V2 as driver
        else:
            from waveshare_epd import epd2in13_V4 as driver
        self.driver = driver
        self.epdconfig = driver.epdconfig
        self.epd = driver.EPD()
        self.is_color = IS_COLOR
        self.colors = DISPLAY_COLORS

    def init(self):
        logging.info("Initializing display driver %s", self.driver.__name__)
        result = self.epd.init()
        if result != 0:
            raise RuntimeError(f"Display driver {self.driver.__name__} initialization failed: {result}")
        logging.info("Display driver initialized: %s", self.driver.__name__)
        return result

    def getbuffer(self, image):
        return self.epd.getbuffer(image)

    def display(self, buffer):
        self.epd.display(buffer)

    def displayPartBaseImage(self, buffer):
        if self.is_color:
            self.epd.display(buffer)
        else:
            self.epd.displayPartBaseImage(buffer)

    def displayPartial(self, buffer):
        if self.is_color:
            self.epd.display(buffer)
        else:
            self.epd.displayPartial(buffer)

    def Clear(self, color=None):
        if color is None:
            color = 0x55 if self.is_color else 0xFF     # white on either panel
        self.epd.Clear(color)

    def sleep(self):
        self.epd.sleep()

    def module_exit(self, cleanup=False):
        self.epdconfig.module_exit(cleanup=cleanup)


def init_display(kind=None):
    """Select the configured driver and return an uninitialized Display."""
    if kind is None:
        kind = settings_display_type()
    if kind == 'b':
        raise ValueError("display_type 'b' is not supported yet: identify the B panel version first")
    if kind == 'auto':
        raise ValueError("display_type 'auto' is no longer supported; set it to 'v4' or 'g'")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError(f"Invalid display_type {kind!r}; choose 'v4', 'g', or 'b'")
    logging.info("Selected display: %s (%s colors), configured as %r",
                 _KINDS[kind], 4 if _KINDS[kind] == 'epd2in13g_V2' else 2, kind)
    return Display(kind)
