#!/usr/bin/env bash
# Bootstrap NodeMonitor on a fresh Raspberry Pi OS install. Safe to re-run.
# Usage: ./scripts/setup_pi.sh [--no-service]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"
RUN_USER="$(id -un)"
INSTALL_SERVICE=1
[[ "${1:-}" == "--no-service" ]] && INSTALL_SERVICE=0

if [[ "$RUN_USER" == "root" ]]; then
    echo "Run as your normal user (not sudo); the script calls sudo when needed." >&2
    exit 1
fi

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y \
    git python3 python3-venv python3-dev python3-pip \
    build-essential libffi-dev libssl-dev \
    bluez libglib2.0-dev

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

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable nmdisplay.service
    sudo systemctl restart nmdisplay.service
    echo "==> Service running. Logs: journalctl -u nmdisplay -f"
fi

echo
echo "Done. Open https://$(hostname).local:5000 (accept the self-signed cert)."
echo "Log out/in (or reboot) once so the dialout/bluetooth group changes apply."