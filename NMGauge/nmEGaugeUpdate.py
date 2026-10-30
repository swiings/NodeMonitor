#!/usr/bin/env python3
import os, sys, time, signal, logging, socket, shutil, json, math, sqlite3
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
    
from waveshare_epd import epd2in13_V4

UPDATE_SECS = 60
PAGE_SECS   = 2
CLEAN_HOUR  = 3        # daily full refresh (the only flash) at 03:00

# --- fonts + draw_metrics() 
FONTDIR = '/usr/share/fonts/truetype/dejavu/'   # sudo apt install fonts-dejavu-core
BOLD    = FONTDIR + 'DejaVuSans-Bold.ttf'
COND    = FONTDIR + 'DejaVuSansCondensed-Bold.ttf'

font10c = ImageFont.truetype(COND, 10)   
font11c = ImageFont.truetype(COND, 11)   
font12c = ImageFont.truetype(COND, 12)   
font14c = ImageFont.truetype(COND, 14)   
font15c = ImageFont.truetype(COND, 15)   
font20c = ImageFont.truetype(COND, 20)   
font23b  = ImageFont.truetype(BOLD, 23)   
font25b  = ImageFont.truetype(BOLD, 25)   
font30b  = ImageFont.truetype(BOLD, 30)   
font50b  = ImageFont.truetype(BOLD, 50)   


# --- drawing functions for the e-paper display
def draw_metrics(draw, m):
    """Draw live metrics onto the 250x122 landscape image (before rotate)."""
    ## top row title, with a timestamp
    draw.text((0, 0), "MESHTASTIC", font=font10c, fill=0, anchor='lt')
    draw.line((0, 10, 63, 10), fill=0) 
    draw.text((125, 0), "NODEMONITOR", font=font15c, fill=0, anchor='mt')
    draw.text((250, 0), m['datetime'], font=font11c, fill=0, anchor='rt')
    draw.line((185, 10, 250, 10), fill=0) 
    ## node count and packet count in bigest font used on the screen
    draw.text((62.5, 15), "NODE COUNT", font=font12c, fill=0, anchor='mt')
    draw.text((187.5, 15), "PACKET COUNT", font=font12c, fill=0, anchor='mt')
    draw.text((62.5, 39), m['total'] + "/" + m['act'] + "/" + m['dir'], font=font23b, fill=0, anchor='mm')
    draw.text((62.5, 54), "TOTAL / ACT / DIR", font=font14c, fill=0, anchor='mt')
    draw.text((187.5, 39), m['packets'], font=font25b, fill=0, anchor='mm')
    draw.text((187.5, 53), m['permin'] + "/m   " + m['perhour'] + "/h", font=font14c, fill=0, anchor='mt')
    ## horizontal line separating node/packet counts from other metrics
    draw.line((0, 67, 250, 67), fill=0)  
    ## metrics related to the reference node used by NodeMonitor
    draw.text((0, 71), "UP", font=font12c, fill=0, anchor='lt')
    draw.text((125, 71), m['uptime'], font=font12c, fill=0, anchor='rt')
    draw.text((0, 83), "NODE" , font=font12c, fill=0, anchor='lt')
    draw.text((125, 83), m['ref_name'], font=font12c, fill=0, anchor='rt')
    draw.text((0, 95), "CH" + m['ref_ch'] + " ON " + m['freq'], font=font12c, fill=0, anchor='lt')
    draw.text((0, 107), "BAT " + m['battery'] + " SD " + m['storage'], font=font12c, fill=0, anchor='lt')
    ## closest/loudest/latest/noisiest section
    draw.text((130, 71), "CLOSEST", font=font12c, fill=0, anchor='lt')
    draw.text((248, 71), m['closest'], font=font12c, fill=0, anchor='rt')  
    draw.text((130, 83), "LOUDEST", font=font12c, fill=0, anchor='lt')
    draw.text((248, 83), m['loudest'], font=font12c, fill=0, anchor='rt')  
    draw.text((130, 95), "LATEST", font=font12c, fill=0, anchor='lt')
    draw.text((248, 95), m['last_heard'], font=font12c, fill=0, anchor='rt')  
    draw.text((130, 107), "MOST", font=font12c, fill=0, anchor='lt')
    draw.text((248, 107), m['noisiest']+" pkts", font=font12c, fill=0, anchor='rt')  

def draw_page2(draw, m):
    """Draw live metrics onto the 250x122 landscape image (2nd page."""
    ## top row title, with a timestamp
    draw.text((0, 0), "MESHTASTIC", font=font10c, fill=0, anchor='lt')
    draw.line((0, 10, 63, 10), fill=0) 
    draw.text((125, 0), "NODEMONITOR", font=font15c, fill=0, anchor='mt')
    draw.text((250, 0), m['datetime'], font=font11c, fill=0, anchor='rt')
    draw.line((185, 10, 250, 10), fill=0) 
    ## node count and packet count 
    draw.text((126, 15), "NODES " + m['total'] + "/" + m['act'] + "/" + m['dir'] +
               "  PACKETS " + m['packets'] + " " + m['permin'] + "/m " + m['perhour'] + "/h", font=font12c, fill=0, anchor='mt')
   
    ## horizontal line separating node/packet counts from other metrics
    draw.line((0, 26, 250, 26), fill=0)  
    ## metrics related to the reference node used by NodeMonitor
    draw.text((0, 35), "CLOSEST ", font=font14c, fill=0, anchor='lt')
    draw.text((88, 35), m['closest_node'], font=font14c, fill=0, anchor='lt')
    draw.text((180, 35), m['closest'], font=font14c, fill=0, anchor='lt')

    draw.text((0, 50), "FARTHEST ", font=font14c, fill=0, anchor='lt')
    draw.text((88, 50), m['farthest_node'], font=font14c, fill=0, anchor='lt')
    draw.text((180, 50), m['farthest'], font=font14c, fill=0, anchor='lt')

    draw.text((0, 65), "LOUDEST ", font=font14c, fill=0, anchor='lt')
    draw.text((88, 65), m['loudest_node'], font=font14c, fill=0, anchor='lt')
    draw.text((180, 65), m['loudest'], font=font14c, fill=0, anchor='lt')

    draw.text((0, 80), "LATEST ", font=font14c, fill=0, anchor='lt')
    draw.text((88, 80), m['last_heard_node'], font=font14c, fill=0, anchor='lt')
    draw.text((180, 80), m['last_heard'], font=font14c, fill=0, anchor='lt')

    draw.text((0, 95), "NOISIEST ", font=font14c, fill=0, anchor='lt')
    draw.text((88, 95), m['noisiest_node'], font=font14c, fill=0, anchor='lt')
    draw.text((180, 95), m['noisiest'], font=font14c, fill=0, anchor='lt')

def draw_page3(draw, m):
    """Draw live metrics onto the 250x122 landscape image (2nd page."""
    ## top row title, with a timestamp
    draw.text((0, 0), "MESHTASTIC", font=font10c, fill=0, anchor='lt')
    draw.line((0, 10, 63, 10), fill=0) 
    draw.text((125, 0), "NODEMONITOR", font=font15c, fill=0, anchor='mt')
    draw.text((250, 0), m['datetime'], font=font11c, fill=0, anchor='rt')
    draw.line((185, 10, 250, 10), fill=0) 
    ## node count and packet count 
    draw.text((125, 13), m['total'] + "/" + m['act'] + "/" + m['dir'], font=font50b, fill=0, anchor='mt')
    draw.text((125, 55), "TOTAL / ACT / DIR NODES", font=font15c, fill=0, anchor='mt')

    draw.text((125, 68), m['packets'], font=font50b, fill=0, anchor='mt')
    draw.text((125, 122), m['permin'] + "/m   " + m['perhour'] + "/h", font=font20c, fill=0, anchor='mb')




# --- PiSugar battery via pisugar-server (TCP 8423)
def get_pisugar(cmd, host='127.0.0.1', port=8423, timeout=2):
    """Send one command to pisugar-server; return the value after ':' or None."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall((cmd + '\n').encode())
            resp = s.recv(64).decode().strip()   # e.g. "battery: 87.53"
        return resp.split(':', 1)[1].strip()
    except Exception as e:
        logging.warning("pisugar '%s' failed: %s", cmd, e)
        return None
    
# --- helper functions for battery and storage display
def battery_str():
    """Battery percent like '87%', with '+' appended while charging."""
    pct = get_pisugar('get battery')
    try:
        s = f"{float(pct):.0f}%"
    except (TypeError, ValueError):
        return '--%'
    if get_pisugar('get battery_charging') == 'true':
        s += '+'
    return s

def storage_str(path='/'):
    """SD card percent used, matching df's Use% (excludes root-reserved blocks)."""
    try:
        du = shutil.disk_usage(path)
        return f"{du.used / (du.used + du.free) * 100:.0f}%"
    except OSError as e:
        logging.warning("disk_usage '%s' failed: %s", path, e)
        return '--%'

base = Image.open('allwhitebackground.png').resize((250, 122)).convert('1')

def render(m, draw_page=draw_metrics):
    img = base.copy()
    draw_page(ImageDraw.Draw(img), m)
    return img.rotate(90, expand=True)     # 270 has Pi0 ports on top, 90 has them on the bottom

# --- NodeMonitor session data (NMData/active.json -> active session SQLite db)
NMDATA = os.environ.get('NM_DATA_DIR',
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'NMData'))

def read_json(name):
    """Load a JSON file from NMData; return {} if missing or unreadable."""
    try:
        with open(os.path.join(NMDATA, name)) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logging.warning("read %s failed: %s", name, e)
        return {}

def haversine_mi(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))

def node_label(node_id, short_name):
    return short_name or f"!{node_id & 0xFFFFFFFF:08x}"

def dhm_str(secs):
    """Duration like '4d 21h 18m'."""
    d, r = divmod(max(int(secs), 0), 86400)
    return f"{d}d {r // 3600}h {r % 3600 // 60}m"

def ms_str(secs):
    """Short elapsed time like '2m 32s', rolling up to '3h 5m' / '2d 4h'."""
    s = max(int(secs), 0)
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"

def db_metrics(now):
    """Query the active session db; any metric that can't be computed is left out."""
    m = {}
    active  = read_json('active.json')
    runtime = read_json('listener_runtime.json')     # written by NMListener on connect
    my_id   = runtime.get('my_node_id')

    if runtime.get('channel_num') is not None:
        m['ref_ch'] = str(runtime['channel_num'])
    if runtime.get('frequency_mhz') is not None:
        m['freq'] = f"{runtime['frequency_mhz']:.3f}mHz"
    if active.get('created'):
        m['uptime'] = dhm_str(now - active['created'])

    if not active.get('db'):
        return m
    try:
        uri = 'file:' + os.path.join(NMDATA, active['db']) + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as e:
        logging.warning("open db failed: %s", e)
        return m
    q = lambda sql, *args: conn.execute(sql, args).fetchone()
    excl = my_id if my_id is not None else -1          # leave the reference node out of node stats
    try:
        m['total'] = str(q("SELECT COUNT(*) FROM nodes")[0])
        m['act']   = str(q("SELECT COUNT(*) FROM nodes WHERE last_heard > ?", now - 3900)[0])
        row = q("""SELECT last_heard, node_id, short_name FROM nodes
                   WHERE (node_id & 4294967295) != ?
                   ORDER BY last_heard DESC, node_id LIMIT 1""", excl)
        if row and row[0]:
            m['last_heard'] = ms_str(now - row[0])
            m['last_heard_node'] = node_label(row[1], row[2])
        # direct = zero hops used (hop_start - hop_limit), same rule as NMDisplay's gauge
        # packet stats count over-the-air packets only: a reference node's own packets
        # arrive over serial with no rx_rssi (and a session may have had several ref nodes)
        m['dir']   = str(q("""SELECT COUNT(DISTINCT from_node_id) FROM packets
                              WHERE rx_rssi IS NOT NULL AND hop_start IS NOT NULL
                                AND hop_start - COALESCE(hop_limit, 0) = 0""")[0])
        m['packets'] = f"{q('SELECT COUNT(*) FROM packets WHERE rx_rssi IS NOT NULL')[0]:,}"
        recent_packets = q(
            "SELECT COUNT(*) FROM packets WHERE rx_rssi IS NOT NULL AND rx_time >= ?",
            now - 3900,
        )[0]
        m['permin']  = f"{recent_packets / 65:.1f}"
        m['perhour'] = str(recent_packets)

        row = q("""SELECT counts.from_node_id, nodes.short_name, counts.n
                   FROM (
                       SELECT from_node_id, COUNT(*) AS n FROM packets
                       WHERE rx_rssi IS NOT NULL
                       GROUP BY from_node_id
                       ORDER BY n DESC, from_node_id LIMIT 1
                   ) AS counts
                   LEFT JOIN nodes ON nodes.node_id = counts.from_node_id""")
        if row:
            m['noisiest'] = f"{row[2]:,}"
            m['noisiest_node'] = node_label(row[0], row[1])

        row = q("""SELECT node_id, short_name, rssi, snr FROM nodes
                   WHERE rssi IS NOT NULL AND (node_id & 4294967295) != ?
                   ORDER BY rssi DESC, node_id LIMIT 1""", excl)
        if row:
            m['loudest'] = f"{row[2]} {row[3]:.1f}" if row[3] is not None else str(row[2])
            m['loudest_node'] = node_label(row[0], row[1])

        if my_id is not None:
            ref = q("SELECT long_name, lat, lon FROM nodes WHERE (node_id & 4294967295) = ?", my_id)
            if ref:
                if ref[0]:
                    m['ref_name'] = ref[0].upper()
                if ref[1] is not None and ref[2] is not None:
                    dists = [
                        (haversine_mi(ref[1], ref[2], lat, lon), node_id, short_name)
                        for node_id, short_name, lat, lon in conn.execute(
                            """SELECT node_id, short_name, lat, lon FROM nodes
                               WHERE lat IS NOT NULL AND lon IS NOT NULL
                               AND (node_id & 4294967295) != ?""",
                            (my_id,),
                        )
                    ]
                    if dists:
                        closest_dist, closest_id, closest_name = min(dists, key=lambda item: item[0])
                        farthest_dist, farthest_id, farthest_name = max(dists, key=lambda item: item[0])
                        m['closest'] = f"{closest_dist:.1f}m"
                        m['closest_node'] = node_label(closest_id, closest_name)
                        m['farthest'] = f"{farthest_dist:.1f}m"
                        m['farthest_node'] = node_label(farthest_id, farthest_name)
    except sqlite3.Error as e:
        logging.warning("db query failed: %s", e)
    finally:
        conn.close()
    return m

# return the dict of values from the active NodeMonitor session; '--' where unavailable
def get_metrics():
    now = int(time.time())
    m = {k: '--' for k in ('total', 'act', 'dir', 'ref_ch', 'ref_name', 'freq', 'uptime', 'packets', 'permin', 'perhour',
                           'closest', 'closest_node',
                           'farthest', 'farthest_node',
                           'loudest', 'loudest_node', 
                           'last_heard', 'last_heard_node', 
                           'noisiest', 'noisiest_node')}

    m.update(db_metrics(now))
    m['datetime'] = datetime.now().strftime('%m/%d %H:%M')
    m['battery']  = battery_str()       # battery percentage with charging status
    m['storage']  = storage_str()       # storage usage percentage
    return m

def main():
    epd = epd2in13_V4.EPD()

    def shutdown(*_):
        epd.sleep()                     # image stays on the panel unpowered
        epd2in13_V4.epdconfig.module_exit(cleanup=True)
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    epd.init()

    # Clear the display initially
    # epd.Clear(0xFF);

    # img = Image.open('allwhitebackground.png').resize((250, 122)).convert('1')
    # epd.displayPartial(epd.getbuffer(img))

    # Display the initial sequence of Haxx Labs images
    img = Image.open('haxxlabsooo1.png').resize((250, 122)).convert('1')
    epd.displayPartBaseImage(epd.getbuffer(img))
    # time.sleep(1)

    img = Image.open('haxxlabsooo2.png').resize((250, 122)).convert('1')
    epd.displayPartial(epd.getbuffer(img))         
    # time.sleep(1)
                
    img = Image.open('haxxlabsooo3.png').resize((250, 122)).convert('1')
    epd.displayPartial(epd.getbuffer(img))
    time.sleep(2)
                
    # Clear the display before showing the "eat sleep" image 
    img = Image.open('allwhitebackground.png').resize((250, 122)).convert('1')
    epd.displayPartBaseImage(epd.getbuffer(img))

    img = Image.open('eatsleep2.png').resize((250, 122)).convert('1').rotate(90, expand=True)
    epd.displayPartial(epd.getbuffer(img))
    time.sleep(2)

    pages = (draw_metrics, draw_page2, draw_page3)
    page_index = 0
    metrics = get_metrics()
    last_update = int(time.time() // UPDATE_SECS)
    img = render(metrics, pages[page_index])
    epd.displayPartBaseImage(epd.getbuffer(img))
    
    last_bytes = img.tobytes()
    last_clean = datetime.now().date()
    
    while True:
        time.sleep(PAGE_SECS)
        update = int(time.time() // UPDATE_SECS)
        if update != last_update:
            metrics = get_metrics()
            last_update = update
        page_index = (page_index + 1) % len(pages)
        img = render(metrics, pages[page_index])
        now = datetime.now()

        if now.hour == CLEAN_HOUR and now.date() != last_clean:
            epd.init()                                    # nightly ghost cleanup
            epd.displayPartBaseImage(epd.getbuffer(img))
            last_clean = now.date()
        elif img.tobytes() != last_bytes:
            epd.displayPartial(epd.getbuffer(img))        # no flash
        last_bytes = img.tobytes()

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    main()