#!/usr/bin/env python3
"""NodeMonitor renderer for the Waveshare 2.13-inch e-Paper HAT V4.

Dependencies:
  sudo apt install python3-pil python3-numpy fonts-dejavu-core
  Install Waveshare's e-Paper Python package/library for your HAT.
"""

import os, sys, time, signal, logging, socket, shutil, json, math, sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd2in13_V4

WIDTH = 250
HEIGHT = 122
BLACK = 0
WHITE = 255

REGULAR_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"
BOLD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
BOLD_ITALIC_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-BoldOblique.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def centered_text(draw, box, text, text_font, fill=BLACK):
    left, top, right, bottom = box
    bounds = draw.textbbox((0, 0), str(text), font=text_font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    x = left + ((right - left) - text_width) / 2 - bounds[0]
    y = top + ((bottom - top) - text_height) / 2 - bounds[1]
    draw.text((round(x), round(y)), str(text), font=text_font, fill=fill)


def fit_font(draw, text, path, max_size, max_width):
    size = max_size
    while size > 8:
        candidate = font(path, size)
        bounds = draw.textbbox((0, 0), str(text), font=candidate)
        if bounds[2] - bounds[0] <= max_width:
            return candidate
        size -= 1
    return font(path, 8)


def draw_rate_card(draw, x, w, label, value):
    draw.rounded_rectangle((x, 26, x + w, 68), radius=10, outline=BLACK, width=1)
    label_font = font(BOLD_ITALIC_FONT, 11)
    value_font = fit_font(draw, value, BOLD_FONT, 25, 43)
    centered_text(draw, (x + 2, 29, x + w, 42), label, label_font)
    centered_text(draw, (x + 2, 40, x + w, 67), value, value_font)


def make_frame(PacketCount, PPM, PPH, UpTime, PacketRate):
    """Return a 250x122 1-bit PIL image without touching the display."""
    image = Image.new("1", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)

    small_italic = font(BOLD_ITALIC_FONT, 10)
    title_font = font(BOLD_ITALIC_FONT, 18)
    packet_label_font = font(BOLD_ITALIC_FONT, 11)
    packet_font = fit_font(draw, PacketCount, BOLD_FONT, 35, 136)

    draw.text((4,5), "MESHTASTIC", font=small_italic, fill=BLACK)
    centered_text(draw, (65, 0, 186, 22), " NodeMonitor", title_font)
    uptime_font = fit_font(draw, UpTime, BOLD_ITALIC_FONT, 12, 61)
    bounds = draw.textbbox((0, 0), str(UpTime), font=uptime_font)
    draw.text((246 - (bounds[2] - bounds[0]), 3), str(UpTime), font=uptime_font, fill=BLACK)
    draw.line((4, 18, 64, 18), fill=BLACK, width=1)
    draw.line((189, 18, 247, 18), fill=BLACK, width=1)

    draw.text((3, 23), "PACKETS", font=packet_label_font, fill=BLACK)
    centered_text(draw, (4, 34, 139, 71), PacketCount, packet_font)
    draw_rate_card(draw, 135, 53, "MINUTE", PPM)
    draw_rate_card(draw, 197, 49, "HOUR", PPH)

    for y in (73, 88, 103):
        for x in range(4, 247, 5):
            draw.point((x, y), fill=BLACK)

    values = [max(0.0, float(value)) for value in list(PacketRate)[:30]]
    values.extend([0.0] * (30 - len(values)))
    peak = max(values, default=0.0)
    for index, value in enumerate(values):
        bar_height = 0 if peak == 0 or value == 0 else max(1, round((value / peak) * 40))
        if bar_height:
            x = 4 + index * 8
            draw.rectangle((x, 118 - bar_height, x + 4, 117), fill=BLACK)

    return image


def load_packet_stats(now=None, data_dir=None):
    """Read air-packet stats; the graph contains 30 trailing one-minute buckets."""
    now = int(time.time()) if now is None else int(now)
    data_dir = Path(data_dir or os.environ.get("NM_DATA_DIR") or
                    Path(__file__).resolve().parent.parent / "NMData")
    stats = {"PacketCount": "--", "PPM": "--", "PPH": "--",
             "UpTime": "--", "PacketRate": [0] * 30}
    try:
        with (data_dir / "active.json").open() as active_file:
            active = json.load(active_file)
        if not isinstance(active, dict):
            raise ValueError("active.json must contain an object")
        if active.get("created") is not None:
            elapsed = max(0, now - int(active["created"]))
            days, remainder = divmod(elapsed, 86400)
            hours, remainder = divmod(remainder, 3600)
            stats["UpTime"] = f"{days}d{hours}h{remainder // 60}m"
        if not active.get("db"):
            return stats
        db_path = (data_dir / active["db"]).resolve()
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            total, recent = conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(rx_time >= ? AND rx_time <= ?), 0)
                   FROM packets WHERE rx_rssi IS NOT NULL""",
                (now - 3900, now),
            ).fetchone()
            start = now - 30 * 60
            rates = [0] * 30
            for bucket, count in conn.execute(
                """SELECT CAST((rx_time - ?) / 60 AS INTEGER), COUNT(*)
                   FROM packets WHERE rx_rssi IS NOT NULL
                   AND rx_time >= ? AND rx_time < ? GROUP BY 1""",
                (start, start, now),
            ):
                rates[bucket] = count
            stats.update(PacketCount=f"{total:,}", PPM=f"{recent / 65:.1f}",
                         PPH=str(recent), PacketRate=rates)
        finally:
            conn.close()
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        logging.warning("Packet stats unavailable: %s", error)
    return stats


def render_display(PacketCount, PPM, PPH, UpTime, PacketRate, epd=None):
    """Draw the values and send one full refresh to an initialized or new V4 display."""
    owns_driver = epd is None
    if owns_driver:
        epd = epd2in13_V4.EPD()
        epd.init()

    image = make_frame(PacketCount, PPM, PPH, UpTime, PacketRate)
    epd.display(epd.getbuffer(image))
    return image


if __name__ == "__main__":
    stats = load_packet_stats()

    display = epd2in13_V4.EPD()
    try:
        display.init()
        render_display(**stats, epd=display)
        display.sleep()
    except KeyboardInterrupt:
        epd2in13_V4.epdconfig.module_exit(cleanup=True)
