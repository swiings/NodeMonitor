"""
bundle_paths.py — Resolve runtime paths for dev and frozen (PyInstaller) modes.

Dev mode  : paths are relative to the source tree (existing behaviour).
Frozen mode: writable files go to ~/Library/Application Support/NodeMonitor/
             and NMListener source lives inside sys._MEIPASS.
"""

import os
import sys

_frozen = getattr(sys, "frozen", False)


def app_support_dir() -> str:
    """~/Library/Application Support/NodeMonitor — always writable."""
    d = os.path.expanduser("~/Library/Application Support/NodeMonitor")
    os.makedirs(d, exist_ok=True)
    return d


def data_dir() -> str:
    """Directory for sessions.json, active.json, and *.db files."""
    if _frozen:
        return app_support_dir()
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "NMData"))


def display_dir() -> str:
    """Writable directory for cert.pem, key.pem, and tile_cache/."""
    if _frozen:
        return app_support_dir()
    return os.path.dirname(os.path.abspath(__file__))


def listener_source_dir() -> str:
    """Path to the NMListener source directory."""
    if _frozen:
        return os.path.join(sys._MEIPASS, "NMListener")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "NMListener"))


def listener_cmd() -> list:
    """
    Command list to launch NMListener as a subprocess.

    Frozen: re-invoke this executable with --listener so launcher.py
            can dispatch to NMListener's main() in the same bundle.
    Dev   : run NMListener/main.py via its venv (or current Python).
    """
    if _frozen:
        return [sys.executable, "--listener"]
    ld = listener_source_dir()
    venv_py = os.path.join(ld, ".venv", "bin", "python3")
    python = venv_py if os.path.exists(venv_py) else sys.executable
    return [python, "-u", os.path.join(ld, "main.py")]


def listener_env() -> dict:
    """
    Extra environment variables to inject into the NMListener subprocess.
    In frozen mode, NM_DATA_DIR tells NMListener where active.json lives.
    """
    if _frozen:
        return {"NM_DATA_DIR": data_dir()}
    return {}
