"""
launcher.py — NodeMonitor .app entry point (PyInstaller windowed bundle).

Two modes, selected by command-line argument:

  (no args)    Start NMDisplay Flask server, then open https://localhost:5000
               in the default browser once the server is accepting connections.

  --listener   Run NMListener's main() directly.  NMDisplay's listener_proc
               spawns a second copy of this executable with this flag.
"""

import os
import sys


def _run_listener() -> None:
    """Add NMListener to sys.path and run its main()."""
    if getattr(sys, "frozen", False):
        listener_dir = os.path.join(sys._MEIPASS, "NMListener")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        listener_dir = os.path.abspath(os.path.join(here, "..", "NMListener"))

    if listener_dir not in sys.path:
        sys.path.insert(0, listener_dir)

    import main as _listener_main
    _listener_main.main()


def _open_browser_when_ready() -> None:
    """Poll https://localhost:5000 and open the browser once it responds."""
    import ssl
    import time
    import urllib.request
    import webbrowser

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for _ in range(60):   # wait up to 60 s
        try:
            urllib.request.urlopen("https://localhost:5000", context=ctx, timeout=1)
            break
        except Exception:
            time.sleep(1)

    webbrowser.open("https://localhost:5000")


def _run_display() -> None:
    """Start the Flask server and open the browser."""
    import threading

    t = threading.Thread(target=_open_browser_when_ready, daemon=True)
    t.start()

    import app as _app
    _app.run_server()


if __name__ == "__main__":
    if "--listener" in sys.argv:
        _run_listener()
    else:
        _run_display()
