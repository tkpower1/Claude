#!/usr/bin/env python3
"""
Kalshi Market-Making Bot – Stress Test Suite
============================================

Runs the bot's strategy against synthetic historical data across 8 market
scenarios and produces a detailed report of risk/return metrics.

Usage:
    python stress_test.py [--budget 1000] [--seed 42] [--verbose]

No Kalshi credentials required (all data is synthetic).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from textwrap import indent

# ── Local imports ────────────────────────────────────────────────────────────
from kalshi_bot.config import BotConfig, MarketFilter, RiskParams, ScoringParams
from kalshi_bot.backtester import Backtester, ScenarioResult
from kalshi_bot.synthetic_data import SCENARIOS


# ── Report helpers ───────────────────────────────────────────────────────────

BAR_WIDTH = 30

def _bar(value: float, lo: float, hi: float, width: int = BAR_WIDTH) -> str:
    """Render a simple ASCII progress bar."""
    frac = max(0.0, min(1.0, (value - lo) / max(hi - lo, 1e-9)))
    filled = round(frac * width)
    return "[" + "█" * filled + "·" * (width - filled) + "]"


def _pnl_bar(pnl: float, scale: float = 50.0) -> str:
    """±-bar centred at 0."""
    half = BAR_WIDTH // 2
    if pnl >= 0:
        filled = min(round(pnl / scale * half), half)
        return "[" + " " * half + "+" * filled + "·" * (half - filled) + "]"
    else:
        filled = min(round(abs(pnl) / scale * half), half)
        return "[" + "·" * (half - filled) + "-" * filled + " " * half + "]"


def _grade(pnl: float, dd: float) -> str:
    """Simple letter grade for a scenario."""
    if pnl > 20 and dd < 0.10:
        return "A+"
    if pnl > 5 and dd < 0.20:
        return "A"
    if pnl > 0 and dd < 0.30:
        return "B"
    if pnl >= 0 and dd < 0.40:
        return "B-"    # flat = capital preserved, no loss
    if pnl > -10 and dd < 0.50:
        return "C"
    return "D"


def format_scenario_report(r: ScenarioResult, verbose: bool = False) -> str:
    lines: list[str] = []

    grade = _grade(r.net_pnl, r.max_portfolio_drawdown)
    pnl_sign = "+" if r.net_pnl >= 0 else ""
    lines += [
        f"  Grade  : {grade}",
        f"  P&L    : {pnl_sign}${r.net_pnl:.2f}  ({pnl_sign}{r.return_pct:.2f}%)  "
        + _pnl_bar(r.net_pnl),
        f"  Budget : ${r.initial_budget:.0f} → ${r.final_budget:.2f}",
        f"  Max DD : {r.max_portfolio_drawdown*100:.1f}%  "
        + _bar(r.max_portfolio_drawdown, 0, 0.5),
    ]

    if verbose:
        lines.append("  Markets:")
        for m in r.markets:
            fr_yes = f"{m.fill_rate_yes*100:.0f}%"
            fr_no  = f"{m.fill_rate_no*100:.0f}%"
            pnl_s  = f"{'+' if m.total_pnl >= 0 else ''}{m.total_pnl:.2f}"
            lines.append(
                f"    {m.ticker:<12} | pos={m.positions_opened:2d} "
                f"both={m.positions_both_filled:2d} "
                f"one={m.positions_one_filled:2d} "
                f"neither={m.positions_neither_filled:2d} "
                f"fill_yes={fr_yes} fill_no={fr_no} "
                f"hold={m.avg_hold_days:.1f}d "
                f"pnl=${pnl_s}"
            )
        lines.append("  Events:")
        for ev in r.events:
            lines.append(f"    {ev}")

    return "\n".join(lines)


def format_full_report(results: list[ScenarioResult], elapsed: float) -> str:
    sep = "─" * 72
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║       KALSHI MARKET-MAKING BOT — STRESS TEST REPORT                 ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        f"  Scenarios run  : {len(results)}",
        f"  Elapsed        : {elapsed:.1f}s",
        "",
        sep,
    ]

    for r in results:
        lines += [
            f"  SCENARIO: {r.scenario_name.upper()}",
            f"  {r.description}",
        ]
        lines.append(format_scenario_report(r, verbose=True))
        lines.append(sep)

    # ── Summary table ────────────────────────────────────────────────────────
    lines += [
        "",
        "  SUMMARY",
        f"  {'Scenario':<22} {'Net P&L':>10} {'Return%':>8} {'MaxDD%':>8} {'Grade':>6}",
        "  " + "-" * 58,
    ]
    for r in results:
        pnl_s = f"{'+' if r.net_pnl >= 0 else ''}{r.net_pnl:.2f}"
        ret_s = f"{'+' if r.return_pct >= 0 else ''}{r.return_pct:.2f}%"
        dd_s  = f"{r.max_portfolio_drawdown*100:.1f}%"
        grade = _grade(r.net_pnl, r.max_portfolio_drawdown)
        lines.append(
            f"  {r.scenario_name:<22} {pnl_s:>10} {ret_s:>8} {dd_s:>8} {grade:>6}"
        )

    profitable = sum(1 for r in results if r.net_pnl > 0)
    all_pnl = sum(r.net_pnl for r in results)
    avg_dd = sum(r.max_portfolio_drawdown for r in results) / max(len(results), 1)

    lines += [
        "  " + "-" * 58,
        f"  {'AGGREGATE':<22} {'+' if all_pnl >= 0 else ''}{all_pnl:.2f} (across all scenarios)",
        f"  Profitable scenarios : {profitable}/{len(results)}",
        f"  Avg max drawdown     : {avg_dd*100:.1f}%",
        "",
    ]

    # ── Key risks ────────────────────────────────────────────────────────────
    lines += [
        sep,
        "  KEY RISK OBSERVATIONS",
        sep,
    ]

    worst = min(results, key=lambda r: r.net_pnl)
    best  = max(results, key=lambda r: r.net_pnl)
    worst_dd = max(results, key=lambda r: r.max_portfolio_drawdown)

    lines += [
        f"  Best scenario   : {best.scenario_name}  (P&L ${best.net_pnl:+.2f})",
        f"  Worst scenario  : {worst.scenario_name}  (P&L ${worst.net_pnl:+.2f})",
        f"  Worst drawdown  : {worst_dd.scenario_name}  ({worst_dd.max_portfolio_drawdown*100:.1f}%)",
        "",
        "  Strategy observations:",
    ]

    # Fill-rate analysis
    all_fill_yes = []
    all_fill_no  = []
    for r in results:
        for m in r.markets:
            all_fill_yes.append(m.fill_rate_yes)
            all_fill_no.append(m.fill_rate_no)

    avg_yes = sum(all_fill_yes) / max(len(all_fill_yes), 1)
    avg_no  = sum(all_fill_no)  / max(len(all_fill_no),  1)

    lines += [
        f"  • Avg YES fill rate across all scenarios : {avg_yes*100:.1f}%",
        f"  • Avg NO  fill rate across all scenarios : {avg_no*100:.1f}%",
        f"  • YES/NO fill rate asymmetry suggests "
        + ("YES-side over-fills (spread too tight or mid drifting YES)"
           if avg_yes > avg_no + 0.05 else
           "NO-side over-fills (spread too tight or mid drifting NO)"
           if avg_no > avg_yes + 0.05 else
           "balanced fill exposure (healthy)"),
    ]

    if worst.net_pnl < -20:
        lines.append(
            f"  • WARNING: '{worst.scenario_name}' scenario exceeded -$20 loss. "
            "Consider tightening market filters or reducing kelly_multiplier."
        )
    if avg_dd > 0.15:
        lines.append(
            f"  • WARNING: Average drawdown {avg_dd*100:.1f}% > 15%. "
            "Consider reducing max_market_fraction."
        )
    loss_scenarios = [r for r in results if r.net_pnl < 0]
    zero_scenarios = [r for r in results if r.net_pnl == 0]
    if not loss_scenarios:
        lines.append(
            f"  • No scenario produced a net loss ({profitable}/{len(results)} profitable, "
            f"{len(zero_scenarios)} flat) – spread-capture constraint is effective."
        )
    elif profitable >= len(results) * 0.75:
        lines.append(
            f"  • Strategy profitable in {profitable}/{len(results)} scenarios."
        )
    else:
        loss_names = ", ".join(r.scenario_name for r in loss_scenarios)
        lines.append(
            f"  • Strategy loses in: {loss_names}. "
            "Review filter thresholds or increase max_fill_cost margin."
        )
    if zero_scenarios:
        zero_names = ", ".join(r.scenario_name for r in zero_scenarios)
        lines.append(
            f"  • Flat (zero P&L) scenarios: {zero_names}."
            " These markets had insufficient fill activity – "
            "consider adding a minimum 24h volume filter ($5k+)."
        )

    lines.append("")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi bot stress test")
    parser.add_argument("--budget",  type=float, default=1000.0)
    parser.add_argument("--seed",    type=int,   default=42)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="Run only this scenario (by name). Omit to run all.",
    )
    parser.add_argument("--log-level", default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)-8s %(name)s  %(message)s",
    )

    config = BotConfig(
        dry_run=True,
        risk=RiskParams(
            total_budget=args.budget,
            kelly_multiplier=0.25,
            max_market_fraction=0.15,
            max_fill_cost=1.02,
            order_levels=3,
            min_order_contracts=1,
        ),
        market_filter=MarketFilter(
            min_mid=0.35,
            max_mid=0.65,
            min_spread=0.03,
            min_days_to_expiry=0,   # backtester controls time
        ),
        scoring=ScoringParams(order_depth_fraction=0.40, default_v=0.06),
    )

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s.name == args.scenario]
        if not scenarios:
            print(f"Unknown scenario '{args.scenario}'. Available:")
            for s in SCENARIOS:
                print(f"  {s.name:25s} – {s.description}")
            sys.exit(1)

    print(f"\nRunning {len(scenarios)} scenario(s) | budget=${args.budget:.0f} | seed={args.seed}")
    print("Please wait…\n")

    t0 = time.perf_counter()
    results: list[ScenarioResult] = []

    for i, scenario in enumerate(scenarios):
        print(f"  [{i+1}/{len(scenarios)}] {scenario.name:<25} … ", end="", flush=True)
        bt = Backtester(config)
        result = bt.run_scenario(scenario, seed=args.seed)
        results.append(result)
        pnl_s = f"{'+' if result.net_pnl >= 0 else ''}{result.net_pnl:.2f}"
        print(f"P&L ${pnl_s}  MaxDD {result.max_portfolio_drawdown*100:.1f}%")

    elapsed = time.perf_counter() - t0
    print(format_full_report(results, elapsed))


if __name__ == "__main__":
    main()
