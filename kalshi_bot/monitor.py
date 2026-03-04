"""
Live position monitor for the Kalshi market-making bot.

Reads bot_state.db and prints a rolling dashboard showing open positions,
budget utilization, and rolling P&L. Refreshes every N seconds.

Usage:
    python -m kalshi_bot.monitor --state-db bot_state.db
    python -m kalshi_bot.monitor --state-db bot_state.db --interval 5
    python -m kalshi_bot.monitor --state-db bot_state.db --once    # single print
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _age_str(last_quote_time: float) -> str:
    secs = max(0, int(time.time() - last_quote_time))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


_LIVE_STATES = {"QUOTING", "YES_FILLED", "NO_FILLED", "ONE_SIDE_HEDGED", "BOTH_FILLED"}


def render(state_db: str) -> str:
    lines: list[str] = []
    lines.append(f"\n{'─'*70}")
    lines.append(f"  Kalshi Bot Monitor  │  {_now_utc()}")
    lines.append(f"{'─'*70}")

    try:
        with sqlite3.connect(state_db) as conn:
            rows = conn.execute(
                """SELECT ticker, title, yes_price, no_price, contracts,
                          state, realised_pnl, last_quote_time,
                          filled_side, hedge_price
                   FROM positions
                   ORDER BY last_quote_time DESC"""
            ).fetchall()
    except Exception as exc:
        lines.append(f"\n  Cannot read {state_db}: {exc}")
        lines.append("  Is the bot running with --state-db set?\n")
        return "\n".join(lines)

    if not rows:
        lines.append(f"\n  No positions found in {state_db}\n")
        return "\n".join(lines)

    live = [r for r in rows if r[5] in _LIVE_STATES]
    closed = [r for r in rows if r[5] == "RESOLVED"]

    # Budget deployed (YES cost + NO cost × contracts, for live positions)
    deployed = sum(
        (r[2] + r[3]) * r[4]
        for r in live
        if r[2] is not None and r[3] is not None
    )

    lines.append(
        f"\n  Live: {len(live)}  │  Closed: {len(closed)}"
        f"  │  Deployed: ${deployed:.2f}"
    )

    # ── Live positions ─────────────────────────────────────────────────
    if live:
        lines.append(f"\n  {'STATE':<20} {'TICKER':<36} {'DETAIL':<30} AGE")
        lines.append("  " + "─" * 96)
        for (ticker, title, yes_p, no_p, contracts,
             state, pnl, qt, filled_side, hedge_price) in live:
            age = _age_str(qt or 0.0)
            yes_p = yes_p or 0.0
            no_p = no_p or 0.0
            hedge_price = hedge_price or 0.0

            if state == "QUOTING":
                detail = f"YES@{yes_p:.3f} NO@{no_p:.3f}  {contracts}ct"
            elif state in ("YES_FILLED", "NO_FILLED", "ONE_SIDE_HEDGED"):
                side = (filled_side or "?").upper()
                detail = f"{side} filled → hedge@{hedge_price:.3f}  {contracts}ct"
            elif state == "BOTH_FILLED":
                detail = f"locked P&L ${pnl:+.4f}  {contracts}ct"
            else:
                detail = ""

            lines.append(
                f"  {state:<20} {ticker:<36} {detail:<30} {age}"
            )

    # ── P&L summary ────────────────────────────────────────────────────
    closed_pnl = sum(r[6] or 0.0 for r in closed)
    live_pnl   = sum(r[6] or 0.0 for r in live)
    total_pnl  = closed_pnl + live_pnl

    lines.append(f"\n  {'P&L SUMMARY':─<68}")
    lines.append(f"  Realised (closed)  : ${closed_pnl:+.4f}  ({len(closed)} positions)")
    lines.append(f"  Locked   (live)    : ${live_pnl:+.4f}  ({len(live)} positions)")

    sign = "+" if total_pnl >= 0 else ""
    lines.append(f"  {'Total':.<20} ${sign}{total_pnl:.4f}")

    if closed:
        wins = sum(1 for r in closed if (r[6] or 0) > 0)
        avg  = closed_pnl / len(closed)
        lines.append(
            f"  Win rate           : {wins}/{len(closed)} ({wins/len(closed):.0%})"
            f"   Avg/trade: ${avg:+.4f}"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi bot position monitor")
    parser.add_argument("--state-db",  default="bot_state.db",
                        help="Path to bot_state.db (default: bot_state.db)")
    parser.add_argument("--interval",  type=int, default=10,
                        help="Refresh interval in seconds (default: 10)")
    parser.add_argument("--once",      action="store_true",
                        help="Print once and exit (non-interactive mode)")
    args = parser.parse_args()

    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    if args.once or not is_tty:
        print(render(args.state_db))
        return

    try:
        while True:
            os.system("clear")
            print(render(args.state_db))
            print(f"  Refreshing every {args.interval}s — Ctrl-C to quit")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
