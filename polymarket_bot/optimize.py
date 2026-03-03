"""
Parameter optimiser for the LP/hedge strategy.

Runs a grid search over key hyperparameters using cached market data,
ranks configurations by risk-adjusted return, and prints a full report.

Usage
-----
python -m polymarket_bot.optimize          # use cached data (fast)
python -m polymarket_bot.optimize --fetch  # re-fetch fresh market data
python -m polymarket_bot.optimize --markets 20 --fetch
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import os
from dataclasses import dataclass
from itertools import product
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from polymarket_bot.backtest import BacktestConfig, BacktestEngine, PortfolioResult, run_backtest
from polymarket_bot.data_fetcher import discover_backtest_markets, MarketHistory


# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------

PARAM_GRID = {
    # How far from mid we post orders (fraction of reward window v)
    # Previous sweep found 0.80 optimal; explore 0.60–0.90 at fine resolution
    "order_depth_fraction": [0.60, 0.70, 0.80, 0.90],
    # Re-quote cadence (minutes) — 15 min was optimal; explore 10–30
    "requote_interval_min": [10, 15, 20, 30],
    # Maximum YES+NO combined cost (has minimal effect; keep two reference points)
    "max_fill_cost": [1.00, 1.02],
    # Number of price-ladder levels per side (1=single order, 2-3=ladder)
    "num_ladder_levels": [1, 2, 3],
}

# Fixed params throughout sweep
FIXED = dict(
    default_v=0.05,
    multiplier=1.0,
    assumed_daily_pool_per_1k=0.20,
    position_size=100.0,
    taker_fee=0.01,
)


# ---------------------------------------------------------------------------
# Sweep result
# ---------------------------------------------------------------------------

@dataclass
class SweepPoint:
    order_depth_fraction: float
    requote_interval_min: int
    max_fill_cost: float
    num_ladder_levels: int

    net_pnl:         float
    reward_income:   float
    fill_pnl:        float
    total_fills:     int
    fill_rate:       float   # fills / total periods
    ann_yield_pct:   float
    sharpe_proxy:    float   # ann_yield / fill_rate (reward per unit of activity)
    scenario_1_pct:  float
    scenario_2_pct:  float
    scenario_3_pct:  float

    def key(self) -> tuple:
        return (self.order_depth_fraction, self.requote_interval_min,
                self.max_fill_cost, self.num_ladder_levels)

    def row(self) -> str:
        return (
            f"  depth={self.order_depth_fraction:.2f}  "
            f"reqt={self.requote_interval_min:3d}m  "
            f"cost={self.max_fill_cost:.2f}  "
            f"lvls={self.num_ladder_levels}  │  "
            f"net=${self.net_pnl:7.4f}  "
            f"fills={self.total_fills:3d}({self.fill_rate*100:4.1f}%)  "
            f"ann={self.ann_yield_pct:6.1f}%  "
            f"s1={self.scenario_1_pct*100:4.1f}%"
        )


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------

def run_sweep(
    markets: list[MarketHistory],
    param_grid: dict = None,
) -> list[SweepPoint]:
    grid = param_grid or PARAM_GRID
    keys = list(grid.keys())
    values = list(grid.values())

    results: list[SweepPoint] = []
    total_combos = 1
    for v in values:
        total_combos *= len(v)

    log = logging.getLogger(__name__)
    log.info("Grid search: %d combinations × %d markets", total_combos, len(markets))

    for combo in product(*values):
        params = dict(zip(keys, combo))
        cfg = BacktestConfig(**{**FIXED, **params})
        portfolio = run_backtest(markets, cfg)

        # Aggregate stats
        total_h = sum(r.span_hours for r in portfolio.market_results)
        total_periods = sum(r.num_periods for r in portfolio.market_results)
        total_fills = sum(
            r.yes_fills + r.no_fills for r in portfolio.market_results
        )
        s1 = sum(r.periods_neither_filled for r in portfolio.market_results)
        s2 = sum(r.periods_one_filled     for r in portfolio.market_results)
        s3 = sum(r.periods_both_filled    for r in portfolio.market_results)

        fill_rate   = total_fills / max(total_periods, 1)
        ann_yield   = portfolio.net_pnl / len(markets) * (8760 / max(total_h / len(markets), 1))
        # Sharpe proxy: annualised yield per unit fill exposure
        sharpe = ann_yield / max(fill_rate * 100, 0.01)

        pt = SweepPoint(
            order_depth_fraction=params["order_depth_fraction"],
            requote_interval_min=params["requote_interval_min"],
            max_fill_cost=params["max_fill_cost"],
            num_ladder_levels=params.get("num_ladder_levels", 1),
            net_pnl=portfolio.net_pnl,
            reward_income=portfolio.total_reward_income,
            fill_pnl=portfolio.total_fill_pnl,
            total_fills=total_fills,
            fill_rate=fill_rate,
            ann_yield_pct=ann_yield,
            sharpe_proxy=sharpe,
            scenario_1_pct=s1 / max(total_periods, 1),
            scenario_2_pct=s2 / max(total_periods, 1),
            scenario_3_pct=s3 / max(total_periods, 1),
        )
        results.append(pt)

        log.debug(
            "depth=%.2f reqt=%3dm cost=%.2f → net=$%.4f fills=%d ann=%.1f%%",
            params["order_depth_fraction"],
            params["requote_interval_min"],
            params["max_fill_cost"],
            portfolio.net_pnl, total_fills, ann_yield,
        )

    return results


# ---------------------------------------------------------------------------
# Sensitivity analysis
# ---------------------------------------------------------------------------

def sensitivity_table(results: list[SweepPoint], param: str) -> str:
    """Average net_pnl and ann_yield grouped by a single parameter."""
    from collections import defaultdict
    groups: dict[float, list[SweepPoint]] = defaultdict(list)
    for r in results:
        key = getattr(r, param)
        groups[key].append(r)

    lines = [f"\n  Sensitivity to '{param}':"]
    lines.append(f"  {'Value':>10}  {'Avg net PnL':>12}  {'Avg ann%':>10}  {'Avg fills':>10}  {'Avg S1%':>8}")
    lines.append("  " + "-" * 58)
    for val in sorted(groups.keys()):
        pts = groups[val]
        avg_pnl    = sum(p.net_pnl for p in pts) / len(pts)
        avg_ann    = sum(p.ann_yield_pct for p in pts) / len(pts)
        avg_fills  = sum(p.total_fills for p in pts) / len(pts)
        avg_s1     = sum(p.scenario_1_pct for p in pts) / len(pts)
        lines.append(
            f"  {val:>10}  ${avg_pnl:>11.4f}  {avg_ann:>9.1f}%  {avg_fills:>10.1f}  {avg_s1*100:>7.1f}%"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[SweepPoint], top_n: int = 10) -> None:
    print("\n" + "=" * 80)
    print("PARAMETER OPTIMISATION REPORT")
    print("=" * 80)

    # Sort by annualised yield
    by_ann = sorted(results, key=lambda r: r.ann_yield_pct, reverse=True)

    print(f"\nTop {top_n} configurations by annualised yield:\n")
    header = (
        "  depth   reqt  maxcost  │  net PnL  fills(rate)   ann%   S1%"
    )
    print(header)
    print("  " + "-" * 72)
    for pt in by_ann[:top_n]:
        print(pt.row())

    # Sort by Sharpe proxy
    by_sharpe = sorted(results, key=lambda r: r.sharpe_proxy, reverse=True)
    print(f"\nTop {top_n} by risk-adjusted return (yield / fill-rate):\n")
    print(header)
    print("  " + "-" * 72)
    for pt in by_sharpe[:top_n]:
        print(pt.row())

    # Sensitivity tables
    for param in PARAM_GRID.keys():
        print(sensitivity_table(results, param))

    # Optimal recommendation
    best = by_ann[0]
    best_sharpe = by_sharpe[0]

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print(f"\n  Max yield config:")
    print(f"    --depth            {best.order_depth_fraction}")
    print(f"    --requote          {best.requote_interval_min}")
    print(f"    --max-fill-cost    {best.max_fill_cost}")
    print(f"    --ladder-levels    {best.num_ladder_levels}")
    print(f"    → Ann. yield: {best.ann_yield_pct:.1f}%  |  fills: {best.total_fills}  |  S1: {best.scenario_1_pct*100:.1f}%")

    print(f"\n  Best risk-adjusted config:")
    print(f"    --depth            {best_sharpe.order_depth_fraction}")
    print(f"    --requote          {best_sharpe.requote_interval_min}")
    print(f"    --max-fill-cost    {best_sharpe.max_fill_cost}")
    print(f"    --ladder-levels    {best_sharpe.num_ladder_levels}")
    print(f"    → Ann. yield: {best_sharpe.ann_yield_pct:.1f}%  |  fills: {best_sharpe.total_fills}  |  S1: {best_sharpe.scenario_1_pct*100:.1f}%")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        prog="optimize",
        description="Grid search over LP/hedge strategy parameters",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--markets", type=int, default=12,
                   help="Number of markets for the sweep")
    p.add_argument("--fetch", action="store_true",
                   help="Bypass cache and fetch fresh market data")
    p.add_argument("--min-near50", type=float, default=0.03)
    p.add_argument("--top", type=int, default=10,
                   help="Number of top configs to show")
    p.add_argument("--json-out", default=None,
                   help="Save sweep results to JSON file")
    p.add_argument("--log-level", default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    print(f"Discovering {args.markets} markets…", flush=True)
    markets = discover_backtest_markets(
        n=args.markets,
        min_ticks=50,
        min_near50_fraction=args.min_near50,
        use_cache=not args.fetch,
    )
    if not markets:
        print("No markets found. Try --fetch.", file=sys.stderr)
        sys.exit(1)

    total_combos = 1
    for v in PARAM_GRID.values():
        total_combos *= len(v)
    grid_dims = " × ".join(str(len(v)) for v in PARAM_GRID.values())
    print(f"Running {grid_dims} = {total_combos} combinations on {len(markets)} markets…",
          flush=True)

    results = run_sweep(markets)
    print_report(results, top_n=args.top)

    if args.json_out:
        import dataclasses
        with open(args.json_out, "w") as f:
            json.dump([dataclasses.asdict(r) for r in results], f, indent=2)
        print(f"Sweep results saved to {args.json_out}")


if __name__ == "__main__":
    main()
