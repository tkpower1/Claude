"""
Historical data replay for the Kalshi market-making backtest.

Loads real market snapshots stored by data_collector.py and converts
them into the MarketSnapshot format expected by the Backtester,
then runs the full strategy simulation against the real price paths.

Usage (from repo root):
    # Replay all tickers with ≥50 snapshots
    python -m kalshi_bot.historical_replay --db market_data.db

    # Replay a single ticker
    python -m kalshi_bot.historical_replay --db market_data.db --ticker KXBTC-25DEC-T99000

    # With custom budget / fee settings
    python -m kalshi_bot.historical_replay --db market_data.db --budget 2000 --fee-rate 0.05
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timezone

from .backtester import Backtester, MarketResult
from .stats import newey_west_ttest
from .config import BotConfig, MarketFilter, RiskParams, ScoringParams
from .synthetic_data import MarketSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_snapshots(db_path: str, ticker: str) -> list[MarketSnapshot]:
    """
    Load all stored snapshots for a ticker sorted by timestamp and convert
    to the MarketSnapshot objects the Backtester expects.

    Time is expressed in days from the first snapshot (t=0).
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT ts, yes_bid, yes_ask, volume_24h, open_interest, mid, spread
               FROM market_snapshots
               WHERE ticker = ?
               ORDER BY ts ASC""",
            (ticker,),
        ).fetchall()

    if not rows:
        return []

    t0 = rows[0][0]
    snaps: list[MarketSnapshot] = []
    for ts, yes_bid, yes_ask, vol, oi, mid, spread in rows:
        yb = yes_bid or 0.0
        ya = yes_ask or 0.0
        snaps.append(MarketSnapshot(
            ticker=ticker,
            t=(ts - t0) / 86_400.0,
            yes_bid=yb,
            yes_ask=ya,
            mid=mid if mid is not None else (yb + ya) / 2,
            spread=spread if spread is not None else (ya - yb),
            volume_usd=vol or 0.0,
            open_interest=oi or 0.0,
        ))
    return snaps


def list_tickers(
    db_path: str,
    min_snapshots: int = 50,
) -> list[tuple[str, int, int, int]]:
    """
    Return (ticker, snapshot_count, first_ts, last_ts) for tickers that have
    at least min_snapshots rows, sorted by count descending.
    """
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            """SELECT ticker, COUNT(*) AS cnt, MIN(ts), MAX(ts)
               FROM market_snapshots
               GROUP BY ticker
               HAVING cnt >= ?
               ORDER BY cnt DESC""",
            (min_snapshots,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Replay runner
# ---------------------------------------------------------------------------

def _make_replay_config(budget: float, fee_rate: float) -> BotConfig:
    return BotConfig(
        dry_run=True,
        risk=RiskParams(
            total_budget=budget,
            kelly_multiplier=0.20,
            max_market_fraction=0.10,
            max_fill_cost=1.02,
            max_order_age=43_200,          # 12 h
            cancel_if_mid_drift=0.07,
            fee_rate=fee_rate,
        ),
        market_filter=MarketFilter(
            min_mid=0.35,
            max_mid=0.65,
            min_spread=0.03,
            min_days_to_expiry=0,          # historical data has no expiry info
        ),
        scoring=ScoringParams(order_depth_fraction=0.40, default_v=0.06),
    )


def run_replay(
    db_path: str,
    config: BotConfig,
    min_snapshots: int = 50,
) -> None:
    """Replay all tickers with sufficient data and print a results table."""
    tickers = list_tickers(db_path, min_snapshots)
    if not tickers:
        print(f"\nNo tickers with ≥{min_snapshots} snapshots in {db_path}")
        print("Run the data collector first:")
        print("  python -m kalshi_bot.data_collector --db market_data.db")
        return

    r = config.risk
    print(
        f"\nHistorical replay | {len(tickers)} markets | budget=${r.total_budget:.0f}\n"
        f"Params: kelly={r.kelly_multiplier}  mf={r.max_market_fraction}  "
        f"age={r.max_order_age//3600}h  drift={r.cancel_if_mid_drift}  "
        f"fee={r.fee_rate*100:.0f}%\n"
    )
    print(
        f"  {'Ticker':<38} {'Snaps':>5}  {'Span':>6}  "
        f"{'Gross P&L':>10}  {'fillY':>5}  {'fillN':>5}"
    )
    print("  " + "-" * 76)

    bt = Backtester(config)
    total_pnl = 0.0
    results: list[tuple[str, int, float, MarketResult]] = []

    for ticker, count, ts_min, ts_max in tickers:
        snaps = load_snapshots(db_path, ticker)
        if len(snaps) < min_snapshots:
            continue
        result = bt.run_market(snaps)
        span_h = (ts_max - ts_min) / 3600.0
        total_pnl += result.total_pnl
        results.append((ticker, count, span_h, result))

        sign = "+" if result.total_pnl >= 0 else ""
        print(
            f"  {ticker:<38} {count:>5}  {span_h:>5.1f}h  "
            f"{sign}{result.total_pnl:>9.2f}  "
            f"{result.fill_rate_yes:>4.0%}  {result.fill_rate_no:>4.0%}"
        )

    if not results:
        return

    print("  " + "-" * 76)
    sign = "+" if total_pnl >= 0 else ""
    print(f"  {'TOTAL':<38} {'':>5}  {'':>6}  {sign}{total_pnl:>9.2f}")

    profitable = sum(1 for _, _, _, r in results if r.total_pnl > 0)
    avg_fy = sum(r.fill_rate_yes for _, _, _, r in results) / len(results)
    avg_fn = sum(r.fill_rate_no  for _, _, _, r in results) / len(results)
    both_pct = sum(r.positions_both_filled for _, _, _, r in results) / max(
        sum(r.positions_opened for _, _, _, r in results), 1
    )

    print(f"\n  Profitable markets : {profitable}/{len(results)}")
    print(f"  Avg YES fill rate  : {avg_fy:.1%}")
    print(f"  Avg NO  fill rate  : {avg_fn:.1%}")
    print(f"  Both-side fill %   : {both_pct:.1%}  ← key profitability driver")

    # Newey-West HAC t-test across per-market P&L series
    pnl_series = [r.total_pnl for _, _, _, r in results]
    tt = newey_west_ttest(pnl_series)
    print(f"\n  Statistical significance (Newey-West HAC):")
    print(f"  {tt.summary()}")
    if tt.significant_5pct:
        print("  ✓ P&L is significant at the 5% level (|t| > 1.96)")
    elif tt.significant_10pct:
        print("  ~ P&L is significant at the 10% level only (|t| > 1.645)")
    else:
        print("  ✗ P&L is NOT statistically significant — may be noise")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay historical Kalshi market data")
    parser.add_argument("--db",            default="market_data.db")
    parser.add_argument("--budget",        type=float, default=1_000.0)
    parser.add_argument("--fee-rate",      type=float, default=0.07,
                        help="Kalshi fee as fraction of cost per fill (default 0.07 = 7%%)")
    parser.add_argument("--min-snapshots", type=int,   default=50,
                        help="Minimum snapshot count to include a market (default 50)")
    parser.add_argument("--ticker",        default=None,
                        help="Replay a single ticker only")
    parser.add_argument("--list",          action="store_true",
                        help="List available tickers and exit")
    parser.add_argument("--log-level",     default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(levelname)s %(message)s")

    if args.list:
        rows = list_tickers(args.db, min_snapshots=1)
        if not rows:
            print("No data found.")
        else:
            print(f"\n  {'Ticker':<38} {'Snaps':>6}  {'Span':>8}")
            print("  " + "-" * 56)
            for ticker, cnt, ts_min, ts_max in rows:
                span_h = (ts_max - ts_min) / 3600.0
                print(f"  {ticker:<38} {cnt:>6}  {span_h:>7.1f}h")
        raise SystemExit(0)

    config = _make_replay_config(args.budget, args.fee_rate)

    if args.ticker:
        snaps = load_snapshots(args.db, args.ticker)
        if not snaps:
            print(f"No data for {args.ticker}")
            raise SystemExit(1)
        bt = Backtester(config)
        result = bt.run_market(snaps)
        sign = "+" if result.total_pnl >= 0 else ""
        print(
            f"\n{args.ticker}: P&L {sign}${result.total_pnl:.2f}  "
            f"positions={result.positions_opened}  "
            f"both_filled={result.positions_both_filled}  "
            f"fillY={result.fill_rate_yes:.0%}  fillN={result.fill_rate_no:.0%}"
        )
    else:
        run_replay(args.db, config, min_snapshots=args.min_snapshots)
