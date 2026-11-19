#!/usr/bin/env python3
"""Driver selection for the Waveshare 2.13-inch e-Paper HAT.

Supports the 2-color V4 panel (epd2in13_V4) and the 4-color "G" panel
(epd2in13g), auto-detecting which one is attached.  After init_display()
(or configure()) the module globals describe the active panel:

    DISPLAY_TYPE    'epd2in13_V4' (2-color) or 'epd2in13g' (4-color)
    DISPLAY_COLORS  2 or 4
    IS_COLOR        True on the 4-color panel
    IMAGE_MODE      PIL mode to draw frames in: '1' (mono) or 'RGB' (color)
    BLACK / WHITE / RED / YELLOW
                    fill values valid for IMAGE_MODE; RED and YELLOW fall
                    back to BLACK on the 2-color panel so the same drawing
                    code renders sensibly on both

Detection works because the two panels have opposite BUSY polarity: the
V4 (SSD1680) BUSY line idles LOW while the G panel (UC8151-family) idles
HIGH, so after a hardware reset one read of the BUSY pin identifies the
panel.  Set "display_type": "v4" or "g" in NMGsettings.json to skip
detection and force a driver.
"""

import json
import logging
import os
import time

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
          'g': 'epd2in13g', 'epd2in13g': 'epd2in13g'}


def configure(kind):
    """Set the module globals for the given driver kind ('v4' or 'g')."""
    global DISPLAY_TYPE, DISPLAY_COLORS, IS_COLOR, IMAGE_MODE
    global BLACK, WHITE, RED, YELLOW
    DISPLAY_TYPE = _KINDS[kind]
    IS_COLOR = DISPLAY_TYPE == 'epd2in13g'
    DISPLAY_COLORS = 4 if IS_COLOR else 2
    IMAGE_MODE = 'RGB' if IS_COLOR else '1'
    if IS_COLOR:
        BLACK, WHITE = (0, 0, 0), (255, 255, 255)
        RED, YELLOW = (255, 0, 0), (255, 255, 0)
    else:
        BLACK, WHITE = 0, 255
        RED = YELLOW = BLACK    # color accents render as black on the mono panel


def settings_display_type(path=SETTINGS_FILE):
    """Return the validated 'display_type' override from settings, or 'auto'."""
    try:
        with open(path) as f:
            value = json.load(f).get('display_type', 'auto')
    except (OSError, ValueError, AttributeError):
        return 'auto'
    if value == 'auto' or value in _KINDS:
        return value
    logging.warning("invalid display_type %r ignored; using auto-detect", value)
    return 'auto'


def detect_display():
    """Identify the attached panel by its BUSY level after a hardware reset.

    The V4 (SSD1680) BUSY line is active-high and idles LOW; the 4-color G
    panel (UC8151-family) BUSY line is active-low and idles HIGH.
    """
    from waveshare_epd import epdconfig
    if epdconfig.module_init() != 0:
        raise RuntimeError('e-paper module init failed')
    try:
        epdconfig.digital_write(epdconfig.RST_PIN, 1)
        epdconfig.delay_ms(50)
        epdconfig.digital_write(epdconfig.RST_PIN, 0)
        epdconfig.delay_ms(5)
        epdconfig.digital_write(epdconfig.RST_PIN, 1)
        # Sample until BUSY holds steady so boot-time toggling can't fool us.
        level, stable_since = None, time.monotonic()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            current = 1 if epdconfig.digital_read(epdconfig.BUSY_PIN) else 0
            if current != level:
                level, stable_since = current, time.monotonic()
            elif time.monotonic() - stable_since >= 0.5:
                break
            epdconfig.delay_ms(10)
    finally:
        epdconfig.module_exit()     # the chosen driver re-inits cleanly
    kind = 'epd2in13g' if level else 'epd2in13_V4'
    logging.info("auto-detected e-paper panel: %s (%s)",
                 kind, '4-color' if level else '2-color')
    return kind


class Display:
    """Wrapper exposing the same surface as the raw Waveshare drivers.

    On the 4-color panel, which has no partial-refresh support, the
    partial-update methods fall back to full (flashing) refreshes.
    """

    def __init__(self, kind):
        configure(kind)
        if IS_COLOR:
            from waveshare_epd import epd2in13g as driver
        else:
            from waveshare_epd import epd2in13_V4 as driver
        self.driver = driver
        self.epdconfig = driver.epdconfig
        self.epd = driver.EPD()
        self.is_color = IS_COLOR
        self.colors = DISPLAY_COLORS

    def init(self):
        return self.epd.init()

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


def init_display(kind='auto'):
    """Resolve the driver (settings override, else BUSY-pin auto-detect),
    set this module's globals, and return an uninitialized Display."""
    if kind in (None, '', 'auto'):
        kind = settings_display_type()
    if kind == 'auto':
        kind = detect_display()
    return Display(kind)
