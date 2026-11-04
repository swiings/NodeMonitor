#!/usr/bin/env bash
# Bootstrap NodeMonitor on a fresh Raspberry Pi OS install. Safe to re-run.
# Usage: ./scripts/setup_pi.sh [--no-service]
#   NM_MDNS_NAME=nodemonitor-car ./scripts/setup_pi.sh   # advertise a different .local name
set -euo pipefail


# need a switch here for installing and setting up NMGauge



REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"
RUN_USER="$(id -un)"
MDNS_NAME="${NM_MDNS_NAME:-nodemonitor}"   # browse to https://$MDNS_NAME.local:5000
INSTALL_SERVICE=1
[[ "${1:-}" == "--no-service" ]] && INSTALL_SERVICE=0

if [[ "$RUN_USER" == "root" ]]; then
    echo "Run as your normal user (not sudo); the script calls sudo when needed." >&2
    exit 1
fi

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip bluez avahi-daemon avahi-utils
# Build headers are only needed if pip can't find a prebuilt aarch64 wheel.
for pkg in python3-dev build-essential libffi-dev libssl-dev; do
    sudo apt-get install -y "$pkg" || echo "   (skipping unavailable optional package $pkg)"
done

echo "==> Granting serial + Bluetooth access to $RUN_USER"
sudo usermod -aG dialout,bluetooth "$RUN_USER"
sudo systemctl enable --now bluetooth

echo "==> Creating virtualenv at $VENV"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install -r "$REPO_DIR/NMDisplay/requirements.txt" \
                        -r "$REPO_DIR/NMListener/requirements.txt"

mkdir -p "$REPO_DIR/NMData" "$REPO_DIR/NMDisplay/tile_cache"

if [[ ! -f "$REPO_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    chmod 600 "$REPO_DIR/.env"
    echo "==> Created $REPO_DIR/.env — add your API keys to it."
fi

if [[ "$INSTALL_SERVICE" == 1 ]]; then
    if [[ -f /etc/systemd/system/nodemonitor.service ]]; then
        sudo systemctl disable --now nodemonitor.service || true
        sudo rm -f /etc/systemd/system/nodemonitor.service
    fi

    echo "==> Installing systemd service nmdisplay.service"
    sudo tee /etc/systemd/system/nmdisplay.service >/dev/null <<EOF
[Unit]
Description=NMDisplay (web UI + NMListener)
After=network-online.target bluetooth.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$REPO_DIR/NMDisplay
ExecStart=$VENV/bin/python3 -u app.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable nmdisplay.service
    sudo systemctl restart nmdisplay.service
    echo "==> Service running. Logs: journalctl -u nmdisplay -f"

    echo "==> Installing nodemonitor-mdns.service (advertises $MDNS_NAME.local)"
    sudo tee /etc/systemd/system/nodemonitor-mdns.service >/dev/null <<EOF
[Unit]
Description=Advertise $MDNS_NAME.local for NodeMonitor
After=avahi-daemon.service network-online.target
Wants=avahi-daemon.service network-online.target

[Service]
Type=simple
ExecStart=/bin/bash $REPO_DIR/scripts/mdns_alias.sh $MDNS_NAME
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl enable --now avahi-daemon
    sudo systemctl daemon-reload
    sudo systemctl enable nodemonitor-mdns.service
    sudo systemctl restart nodemonitor-mdns.service
fi

echo
if [[ "$INSTALL_SERVICE" == 1 ]]; then
    echo "Done. Open https://$MDNS_NAME.local:5000 (accept the self-signed cert)."
    echo "      or https://$(hostname).local:5000 if another NodeMonitor on this network already uses $MDNS_NAME.local"
else
    echo "Done. Open https://$(hostname).local:5000 (accept the self-signed cert)."
fi
echo "Log out/in (or reboot) once so the dialout/bluetooth group changes apply."