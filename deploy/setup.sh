#!/usr/bin/env bash
# deploy/setup.sh — one-shot server setup for the Polymarket LP bot
# Run as root on a fresh Debian/Ubuntu VPS (e.g. Hetzner CAX11)
#
# Usage:
#   curl -sSL <raw-url>/deploy/setup.sh | bash
#   # OR after cloning:
#   sudo bash deploy/setup.sh
set -euo pipefail

REPO_DIR="/opt/polybot"
SERVICE="polybot"
echo "==> Installing system packages"
apt-get update -qq
# Try python3.11 first; fall back to whatever python3 is available
if apt-cache show python3.11 &>/dev/null; then
    apt-get install -y -qq python3.11 python3.11-venv python3-pip git
    PYTHON="python3.11"
else
    apt-get install -y -qq python3 python3-venv python3-pip git
    PYTHON="python3"
fi

echo "==> Creating botuser"
id -u botuser &>/dev/null || useradd -r -s /bin/false -d "$REPO_DIR" botuser

echo "==> Cloning / updating repo"
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull
else
    git clone https://github.com/tkpower1/Claude.git "$REPO_DIR"
fi
chown -R botuser:botuser "$REPO_DIR"

echo "==> Setting up Python venv"
sudo -u botuser $PYTHON -m venv "$REPO_DIR/venv"
sudo -u botuser "$REPO_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

echo "==> Installing systemd service"
cp "$REPO_DIR/deploy/polybot.service" /etc/systemd/system/polybot.service
systemctl daemon-reload
systemctl enable $SERVICE

echo ""
echo "============================================================"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "   1. Create /opt/polybot/.env with your credentials:"
echo "        PRIVATE_KEY=0x..."
echo "        API_KEY=..."
echo "        API_SECRET=..."
echo "        API_PASSPHRASE=..."
echo "        FUNDER=0x..."
echo ""
echo "   2. Start the bot:"
echo "        sudo systemctl start polybot"
echo ""
echo "   3. Watch logs:"
echo "        sudo journalctl -u polybot -f"
echo "============================================================"
