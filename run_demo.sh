#!/usr/bin/env bash
# run_demo.sh — Start the collector + bot in Kalshi demo mode (paper trading).
#
# Prerequisites:
#   1. Copy .env.example to .env and fill in your API credentials.
#   2. Set KALSHI_DEMO=true in .env (or pass --demo below).
#   3. pip install -r requirements.txt
#
# Usage:
#   ./run_demo.sh              # interactive: runs in current terminal
#   ./run_demo.sh --budget 500 # override budget
#
# Both processes write logs to logs/. Stop with Ctrl-C (kills both).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$REPO_DIR/logs"
DATA_DB="$REPO_DIR/market_data.db"
STATE_DB="$REPO_DIR/bot_state.db"
ENV_FILE="$REPO_DIR/.env"

mkdir -p "$LOG_DIR"

# Load .env if present
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    source "$ENV_FILE"
    set +o allexport
fi

# Force demo + dry-run safety net (remove --dry-run to submit real demo orders)
export KALSHI_DEMO="${KALSHI_DEMO:-true}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Kalshi Bot  │  demo=$KALSHI_DEMO  dry_run=${KALSHI_DRY_RUN:-false}"
echo "  State DB    │  $STATE_DB"
echo "  Market DB   │  $DATA_DB"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Trap Ctrl-C and kill both background jobs
cleanup() {
    echo ""
    echo "Stopping collector and bot…"
    kill "$COLLECTOR_PID" "$BOT_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup INT TERM

# Start collector (background)
python -m kalshi_bot.data_collector \
    --db "$DATA_DB" \
    --interval 60 \
    --log-level INFO \
    >> "$LOG_DIR/collector.log" 2>&1 &
COLLECTOR_PID=$!
echo "Collector PID $COLLECTOR_PID → $LOG_DIR/collector.log"

# Start bot (background)
python -m kalshi_bot \
    --demo \
    --dry-run \
    --state-db "$STATE_DB" \
    --log-level INFO \
    "$@" \
    >> "$LOG_DIR/bot.log" 2>&1 &
BOT_PID=$!
echo "Bot       PID $BOT_PID → $LOG_DIR/bot.log"

echo ""
echo "Monitor positions:"
echo "  python -m kalshi_bot.monitor --state-db $STATE_DB"
echo ""
echo "Tail logs:"
echo "  tail -f $LOG_DIR/bot.log"
echo "  tail -f $LOG_DIR/collector.log"
echo ""
echo "Press Ctrl-C to stop both processes."

wait
