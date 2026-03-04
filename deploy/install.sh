#!/usr/bin/env bash
# deploy/install.sh — Install the bot as systemd services on a Linux server.
#
# Run as root (or with sudo) on the target machine:
#   sudo bash deploy/install.sh
#
# Assumptions:
#   - Repo checked out at /opt/kalshi-bot
#   - Python venv at /opt/kalshi-bot/.venv
#   - API credentials at /opt/kalshi-bot/.env
#   - A dedicated system user named 'kalshi' exists

set -euo pipefail

INSTALL_DIR="/opt/kalshi-bot"
DATA_DIR="/var/lib/kalshi"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_USER="kalshi"

echo "=== Kalshi Bot Installer ==="

# Create data directory
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
echo "Data directory: $DATA_DIR"

# Install service files
cp "$INSTALL_DIR/deploy/kalshi-collector.service" "$SYSTEMD_DIR/"
cp "$INSTALL_DIR/deploy/kalshi-bot.service"       "$SYSTEMD_DIR/"
echo "Service files installed."

# Reload and enable
systemctl daemon-reload
systemctl enable kalshi-collector kalshi-bot

echo ""
echo "To start:"
echo "  sudo systemctl start kalshi-collector"
echo "  sudo systemctl start kalshi-bot"
echo ""
echo "To check status:"
echo "  sudo systemctl status kalshi-collector kalshi-bot"
echo "  sudo journalctl -u kalshi-bot -f"
echo ""
echo "Monitor positions:"
echo "  python -m kalshi_bot.monitor --state-db $DATA_DIR/bot_state.db"
