"""
Backtest CLI: python -m polymarket_bot.run_backtest [options]

Fetches real Polymarket price histories, replays the LP/hedge strategy,
and prints a full P&L report.

Usage examples
--------------
# Quick run with defaults (fetches ~8 recent markets)
python -m polymarket_bot.run_backtest

# Custom config: larger position, lower reward assumption
python -m polymarket_bot.run_backtest \\
    --position-size 500 \\
    --daily-pool 0.10 \\
    --requote 30 \\
    --depth 0.02 \\
    --max-fill-cost 1.02

# Use saved market data (no live API calls)
python -m polymarket_bot.run_backtest --use-cache
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# Allow running as `python -m polymarket_bot.run_backtest` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polymarket_bot.backtest import BacktestConfig, run_backtest
from polymarket_bot.data_fetcher import (
    discover_backtest_markets,
    MarketHistory,
    PriceTick,
    fetch_price_history,
    build_market_history,
    fetch_market_list,
)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("urllib3", "urllib", "http"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_backtest",
        description="Polymarket LP/Hedge Bot — Historical Backtest",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--markets", type=int, default=8,
                   help="Number of markets to test")
    p.add_argument("--position-size", type=float, default=100.0,
                   help="USDC notional per market position")
    p.add_argument("--requote", type=int, default=60,
                   help="Re-quote interval in minutes")
    p.add_argument("--depth", type=float, default=None,
                   help="Order depth fraction of v (default: 0.40 × v=0.05)")
    p.add_argument("--v", type=float, default=0.05,
                   help="Reward window v (probability units)")
    p.add_argument("--max-fill-cost", type=float, default=1.02,
                   help="Max profitable YES+NO combined cost")
    p.add_argument("--daily-pool", type=float, default=0.20,
                   help="Assumed daily USDC reward pool per $1k deployed")
    p.add_argument("--taker-fee", type=float, default=0.01,
                   help="Taker fee fraction applied on hedge fills")
    p.add_argument("--ladder-levels", type=int, default=1,
                   help="Number of price-ladder levels per side (1=single order, 2-3=ladder)")
    p.add_argument("--use-cache", action="store_true",
                   help="Use cached price histories (no live API calls)")
    p.add_argument("--min-near50", type=float, default=0.05,
                   help="Min fraction of time price must be near 50/50")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--json-out", type=str, default=None,
                   help="Save full results to this JSON file")
    return p.parse_args()


def _results_to_dict(portfolio) -> dict:
    """Serialise PortfolioResult to plain dict for JSON output."""
    out = {
        "net_pnl": round(portfolio.net_pnl, 6),
        "total_reward_income": round(portfolio.total_reward_income, 6),
        "total_fill_pnl": round(portfolio.total_fill_pnl, 6),
        "total_fees": round(portfolio.total_fees, 6),
        "markets": [],
    }
    for r in portfolio.market_results:
        mr = {
            "question": r.question,
            "condition_id": r.condition_id,
            "resolved_yes": r.resolved_yes,
            "span_hours": r.span_hours,
            "num_periods": r.num_periods,
            "scenario_1_neither": r.periods_neither_filled,
            "scenario_2_one_fill": r.periods_one_filled,
            "scenario_3_both_fill": r.periods_both_filled,
            "yes_fills": r.yes_fills,
            "no_fills": r.no_fills,
            "profitable_fills": r.profitable_fills,
            "unprofitable_fills": r.unprofitable_fills,
            "reward_income": round(r.total_reward_income, 6),
            "fill_pnl": round(r.total_fill_pnl, 6),
            "fees": round(r.total_fees, 6),
            "net_pnl": round(r.net_pnl, 6),
            "annualised_yield_pct": round(r.net_pnl * (8760 / max(r.span_hours, 1)), 4),
            "periods": [
                {
                    "start_ts": p.start_ts,
                    "mid_open": round(p.mid_open, 4),
                    "yes_bid": round(p.yes_bid, 4),
                    "no_bid": round(p.no_bid, 4),
                    "low": round(p.period_low, 4),
                    "high": round(p.period_high, 4),
                    "yes_filled": p.yes_filled,
                    "no_filled": p.no_filled,
                    "reward": round(p.reward_income, 6),
                    "fill_pnl": round(p.fill_pnl, 6),
                }
                for p in r.periods
            ],
        }
        out["markets"].append(mr)
    return out


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)
    log = logging.getLogger(__name__)

    # Build backtest config
    depth_frac = args.depth if args.depth is not None else 0.40
    cfg = BacktestConfig(
        requote_interval_min=args.requote,
        order_depth_fraction=depth_frac,
        default_v=args.v,
        assumed_daily_pool_per_1k=args.daily_pool,
        position_size=args.position_size,
        max_fill_cost=args.max_fill_cost,
        taker_fee=args.taker_fee,
        num_ladder_levels=args.ladder_levels,
    )

    # Discover markets
    log.info("Discovering backtest markets…")
    markets = discover_backtest_markets(
        n=args.markets,
        min_ticks=50,
        min_near50_fraction=args.min_near50,
        use_cache=args.use_cache,
    )

    if not markets:
        log.error("No markets found. Try --use-cache or check network.")
        sys.exit(1)

    log.info("Running backtest on %d markets…", len(markets))
    portfolio = run_backtest(markets, cfg)

    # Print full report
    print()
    print(portfolio.portfolio_summary())
    print()

    # Per-market detail
    for r in portfolio.market_results:
        print(r.summary())
        print()

    # JSON output
    if args.json_out:
        out = _results_to_dict(portfolio)
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2)
        log.info("Results saved to %s", args.json_out)


if __name__ == "__main__":
    main()
