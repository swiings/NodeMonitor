# NodeMonitor.spec — PyInstaller build spec
#
# Build from the repo root:
#   pip install pyinstaller
#   pip install -r NMDisplay/requirements.txt -r NMListener/requirements.txt
#   pyinstaller NodeMonitor.spec
#
# Output: dist/NodeMonitor.app

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["NMDisplay/launcher.py"],
    pathex=["NMDisplay"],          # lets PyInstaller find sessions, queries, etc.
    binaries=[],
    datas=[
        # Flask templates and static assets
        ("NMDisplay/templates", "templates"),
        ("NMDisplay/static",    "static"),
        # Entire NMListener source tree (run as a subprocess via --listener flag)
        ("NMListener",          "NMListener"),
    ],
    hiddenimports=[
        # Meshtastic and its dependencies pull in a lot of dynamic imports
        "meshtastic",
        "meshtastic.tcp_interface",
        "meshtastic.serial_interface",
        "meshtastic.ble_interface",
        "meshtastic.mesh_pb2",
        "meshtastic.portnums_pb2",
        "meshtastic.telemetry_pb2",
        "pypubsub",
        "pubsub",
        "pubsub.core",
        # Flask internals
        "flask",
        "werkzeug",
        "jinja2",
        "itsdangerous",
        "click",
        # cryptography (for self-signed cert generation)
        "cryptography",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.asymmetric.rsa",
        "cryptography.x509",
        # requests (for OSM tile proxy)
        "requests",
        "urllib3",
        "charset_normalizer",
        "certifi",
        "idna",
        # protobuf
        "google.protobuf",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NodeMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,      # no terminal window — browser-based UI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,   # inherit from build machine (macos-13 = x86_64)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NodeMonitor",
)

app = BUNDLE(
    coll,
    name="NodeMonitor.app",
    icon=None,
    bundle_identifier="com.nodemonitor.app",
    info_plist={
        "CFBundleName":             "NodeMonitor",
        "CFBundleDisplayName":      "NodeMonitor",
        "CFBundleVersion":          "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable":  True,
        # Allow network access (for Meshtastic TCP + OSM tiles)
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    },
)
