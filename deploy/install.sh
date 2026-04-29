#!/usr/bin/env bash
# One-shot installer for Ubuntu. Idempotent — safe to re-run.
#
# Usage:
#   sudo bash deploy/install.sh
#
# Creates /opt/weatherbet, a 'weatherbet' system user, a venv, installs deps,
# copies the systemd unit, and enables the service. Does NOT start it — flip
# live_trading and fill .env first, then `systemctl start weatherbet`.

set -euo pipefail

INSTALL_DIR="/opt/weatherbet"
SERVICE_USER="weatherbet"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root (sudo)." >&2
  exit 1
fi

apt-get update -qq
apt-get install -y python3-venv python3-pip rsync

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude '.git' --exclude 'data' --exclude '__pycache__' --exclude '.venv' \
  "$SRC_DIR/" "$INSTALL_DIR/"

# venv
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# data dir + .env scaffolding
mkdir -p "$INSTALL_DIR/data"
[[ -f "$INSTALL_DIR/.env" ]] || cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# systemd
cp "$INSTALL_DIR/deploy/weatherbet.service" /etc/systemd/system/weatherbet.service
systemctl daemon-reload
systemctl enable weatherbet.service

echo
echo "Installed to $INSTALL_DIR"
echo "Next:"
echo "  1. Edit  $INSTALL_DIR/.env           (private key + funder address)"
echo "  2. Edit  $INSTALL_DIR/config.json    (set live_trading + risk caps)"
echo "  3. Test  sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/setup_wallet.py"
echo "  4. Start sudo systemctl start weatherbet"
echo "  5. Logs  journalctl -u weatherbet -f"
