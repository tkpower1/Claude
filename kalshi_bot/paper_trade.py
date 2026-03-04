"""
Paper-trade runner for the pre-resolution volatility-spike hypothesis.

Hypothesis
──────────
Markets in the 72 hours before resolution experience:
  1. Volume spikes as participants trade final conviction.
  2. Spread widening as market makers pull back on resolution uncertainty.
  3. OU mean-reversion keeps mid near the true probability → hedges fill fast.
  4. Short adverse-selection window (market resolves soon, can't be wrong long).

The condition to enter:
  rv_6h / rv_7d  >  min_vol_ratio   (short-term spike vs baseline)
  days_to_close  ≤  max_days        (pre-resolution window)
  spread         >  fee_break_even  (unit economics positive after 7% fee)

What this script does
──────────────────────
  1. Runs the bot in dry_run=True mode (no real orders placed).
  2. Every fill event is logged to paper_trade_fills.csv.
  3. Every scan is logged to paper_trade_scans.csv for post-analysis.
  4. A live summary prints to stdout every 5 minutes.

Usage
──────
  python -m kalshi_bot.paper_trade [options]

  --db       market_data.db      SQLite file where data_collector writes snapshots
  --days     3                   Max days to expiry (pre-resolution window)
  --ratio    1.5                 Min vol ratio (spike threshold)
  --budget   1000                Simulated budget (USD)
  --hours    72                  How long to run (0 = until Ctrl-C)
  --demo                         Use Kalshi demo environment

Interpreting results after 2–4 weeks
──────────────────────────────────────
  Look for:
    • fill_rate_both  > 10%  (both sides filling, not just directional)
    • pnl_per_fill   > 0     (positive after simulated 7% fee)
    • vol_at_entry   > baseline_vol × min_vol_ratio  (hypothesis confirmed)
    • t_stat         > 1.96  (Newey-West, statistically significant edge)

  If fill_rate_both < 5%: too deep, widen depth_fraction.
  If pnl_per_fill  < 0:   fee too high for the spread being captured.
  If t_stat < 1.645:      not enough data yet, or no edge (wait longer).
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .bot import MarketMakingBot
from .config import BotConfig, MarketFilter, RiskParams, ScoringParams
from .stats import newey_west_ttest

logger = logging.getLogger(__name__)

# CSV column headers
_FILLS_COLS = [
    "ts", "ticker", "side", "price", "contracts",
    "yes_price", "no_price", "vol_ratio", "days_to_close",
    "spread", "state_after",
]
_SCANS_COLS = [
    "ts", "markets_fetched", "markets_passed_filter",
    "vol_spikes_detected", "positions_opened",
    "budget_available", "budget_allocated",
]


class PaperTradeLogger:
    """Writes fills and scan summaries to CSV files for later analysis."""

    def __init__(self, out_dir: str = ".") -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._fills_path = self.out_dir / f"paper_fills_{ts}.csv"
        self._scans_path = self.out_dir / f"paper_scans_{ts}.csv"

        self._fills = open(self._fills_path, "w", newline="")
        self._scans = open(self._scans_path, "w", newline="")

        self._fill_writer = csv.DictWriter(self._fills, fieldnames=_FILLS_COLS)
        self._scan_writer = csv.DictWriter(self._scans, fieldnames=_SCANS_COLS)

        self._fill_writer.writeheader()
        self._scan_writer.writeheader()

        self._pnl_series: list[float] = []

    def log_fill(self, row: dict) -> None:
        row["ts"] = datetime.now(tz=timezone.utc).isoformat()
        self._fill_writer.writerow({k: row.get(k, "") for k in _FILLS_COLS})
        self._fills.flush()

    def log_scan(self, row: dict) -> None:
        row["ts"] = datetime.now(tz=timezone.utc).isoformat()
        self._scan_writer.writerow({k: row.get(k, "") for k in _SCANS_COLS})
        self._scans.flush()

    def add_pnl(self, pnl: float) -> None:
        self._pnl_series.append(pnl)

    def summary(self) -> str:
        lines = [
            f"  Fills logged  : {self._fills_path}",
            f"  Scans logged  : {self._scans_path}",
        ]
        if len(self._pnl_series) >= 2:
            tt = newey_west_ttest(self._pnl_series)
            lines.append(
                f"  P&L t-test    : {tt.summary()}"
            )
            lines.append(
                f"  Significance  : {'✓ p<0.05 (edge detected)' if tt.significant_5pct else '✗ not yet significant'}"
            )
        else:
            lines.append("  P&L t-test    : need ≥2 closed positions")
        return "\n".join(lines)

    def close(self) -> None:
        self._fills.close()
        self._scans.close()


def _build_config(args: argparse.Namespace) -> BotConfig:
    """Build a paper-trade-specific BotConfig from CLI arguments."""
    return BotConfig(
        dry_run=True,
        demo=args.demo,
        market_filter=MarketFilter(
            min_mid=0.35,
            max_mid=0.65,
            min_spread=0.06,          # fee-break-even floor
            max_open_interest=100_000,
            min_days_to_expiry=0,     # allow same-day resolution
            max_days_to_expiry=args.days,
        ),
        scoring=ScoringParams(
            order_depth_fraction=0.60,  # depth=4.8¢ at default_v=0.08 → fee-profitable
            default_v=0.08,
            min_vol_ratio=args.ratio,
        ),
        risk=RiskParams(
            total_budget=args.budget,
            max_fill_cost=0.935,        # enforce fee-profitable spread capture
            kelly_multiplier=0.20,
            max_market_fraction=0.10,
            max_order_age=7_200,        # 2h max quote age (pre-resolution moves fast)
            cancel_if_mid_drift=0.05,   # tighter cancel on drift
            hedge_stop_gap=0.10,
            fee_rate=0.07,
        ),
        scan_interval=60,
        report_interval=300,
    )


def run_paper_trade(args: argparse.Namespace) -> None:
    """Main paper-trade loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = _build_config(args)
    pt_log = PaperTradeLogger(out_dir="paper_trade_results")

    print("\n" + "═" * 68)
    print("  PAPER TRADE — Pre-resolution vol-spike strategy")
    print("═" * 68)
    print(f"  Hypothesis : rv_6h / rv_7d ≥ {args.ratio}× AND days_to_close ≤ {args.days}")
    print(f"  Budget     : ${cfg.risk.total_budget:,.0f} (simulated, no real orders)")
    print(f"  Min spread : {cfg.market_filter.min_spread:.0%} (fee break-even floor)")
    print(f"  Depth      : {cfg.scoring.default_v * cfg.scoring.order_depth_fraction:.3f} "
          f"(default_v={cfg.scoring.default_v} × frac={cfg.scoring.order_depth_fraction})")
    print(f"  Fee rate   : {cfg.risk.fee_rate:.0%} | net/pair: "
          f"{(1 - 2*cfg.scoring.default_v*cfg.scoring.order_depth_fraction) - cfg.risk.fee_rate * (1 - 2*cfg.scoring.default_v*cfg.scoring.order_depth_fraction):+.4f}")
    print(f"  Environment: {'DEMO' if args.demo else 'LIVE (dry_run=True, no orders sent)'}")
    print(f"  DB         : {args.db}")
    print("═" * 68 + "\n")

    if args.demo:
        os.environ.setdefault("KALSHI_DEMO", "true")

    data_db = args.db if Path(args.db).exists() else None
    if data_db is None:
        logger.warning(
            "market_data.db not found at '%s' — vol_ratio filter will be disabled. "
            "Run data_collector first: python -m kalshi_bot.data_collector --db %s",
            args.db, args.db,
        )

    bot = MarketMakingBot(cfg, data_db=data_db)

    # Monkey-patch the bot to also log fills to our CSV
    orig_refresh = bot.order_mgr.refresh_all
    prev_positions: dict = {}

    def _refresh_and_log() -> None:
        before = {
            t: (p.state, p.realised_pnl)
            for t, p in bot.order_mgr.positions.items()
        }
        orig_refresh()
        for ticker, pos in bot.order_mgr.positions.items():
            b_state, b_pnl = before.get(ticker, (None, 0.0))
            if pos.realised_pnl != b_pnl and pos.realised_pnl != 0:
                pnl = pos.realised_pnl - b_pnl
                pt_log.add_pnl(pnl)
                pt_log.log_fill({
                    "ticker": ticker,
                    "side": pos.filled_side or "both",
                    "price": pos.yes_price,
                    "contracts": pos.contracts,
                    "yes_price": pos.yes_price,
                    "no_price": pos.no_price,
                    "state_after": pos.state.name,
                })

    bot.order_mgr.refresh_all = _refresh_and_log

    stop_at = time.time() + args.hours * 3600 if args.hours > 0 else float("inf")

    def _signal_handler(sig, frame):
        bot.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    bot._running = True
    if bot._ws is not None:
        bot._ws.start()

    last_report = 0.0
    tick = 0

    try:
        while bot._running and time.time() < stop_at:
            tick += 1
            tick_start = time.time()

            try:
                bot._tick()
            except Exception as exc:
                logger.error("Tick %d error: %s", tick, exc, exc_info=True)

            pt_log.log_scan({
                "markets_fetched": 0,   # populated from bot logs
                "markets_passed_filter": 0,
                "vol_spikes_detected": 0,
                "positions_opened": len(bot.order_mgr.positions),
                "budget_available": round(bot.budget.available, 2),
                "budget_allocated": round(
                    cfg.risk.total_budget - bot.budget.available, 2
                ),
            })

            if time.time() - last_report >= 300:
                print(f"\n{'─'*56}")
                print(f"  {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  |  tick={tick}")
                print(bot.order_mgr.summary())
                print(pt_log.summary())
                last_report = time.time()

            elapsed = time.time() - tick_start
            sleep_for = max(0.0, cfg.scan_interval - elapsed)
            if sleep_for > 0 and bot._running:
                time.sleep(sleep_for)

    finally:
        if bot._ws is not None:
            bot._ws.stop()
        pt_log.close()
        print(f"\n{'═'*56}")
        print("  Paper trade ended.")
        print(pt_log.summary())
        print(f"{'═'*56}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper-trade the pre-resolution vol-spike strategy on Kalshi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db", default="market_data.db",
        help="Path to market_data.db (from data_collector). Default: market_data.db",
    )
    parser.add_argument(
        "--days", type=int, default=3,
        help="Maximum days to expiry for target markets (default: 3)",
    )
    parser.add_argument(
        "--ratio", type=float, default=1.5,
        help="Minimum rv_6h/rv_7d ratio to enter (vol spike threshold, default: 1.5)",
    )
    parser.add_argument(
        "--budget", type=float, default=1_000.0,
        help="Simulated budget in USD (default: 1000)",
    )
    parser.add_argument(
        "--hours", type=float, default=0,
        help="How many hours to run (0 = run until Ctrl-C, default: 0)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use Kalshi demo environment",
    )
    run_paper_trade(parser.parse_args())
