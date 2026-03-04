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

def _make_config(
    budget: float,
    depth: float,
    kelly: float,
    mf: float,
    max_order_age: int = 14400,   # 4 hours in seconds
    hedge_stop_gap: float = 0.12,
) -> BotConfig:
    return BotConfig(
        dry_run=True,
        risk=RiskParams(
            total_budget=budget,
            kelly_multiplier=kelly,
            max_market_fraction=mf,
            max_fill_cost=1.02,
            order_levels=3,
            min_order_contracts=1,
            max_order_age=max_order_age,
            hedge_stop_gap=hedge_stop_gap,
        ),
        market_filter=MarketFilter(
            min_mid=0.35,
            max_mid=0.65,
            min_spread=0.03,
            min_days_to_expiry=0,
        ),
        scoring=ScoringParams(order_depth_fraction=depth, default_v=0.06),
    )


def _run_all(
    scenarios: list,
    config: BotConfig,
    seed: int,
    label: str,
    verbose: bool = False,
) -> tuple[list[ScenarioResult], float]:
    """Run all scenarios and return (results, elapsed)."""
    t0 = time.perf_counter()
    results: list[ScenarioResult] = []
    for i, scenario in enumerate(scenarios):
        print(f"  [{label}] {scenario.name:<25} … ", end="", flush=True)
        bt = Backtester(config)
        result = bt.run_scenario(scenario, seed=seed)
        results.append(result)
        pnl_s = f"{'+' if result.net_pnl >= 0 else ''}{result.net_pnl:.2f}"
        print(f"P&L ${pnl_s}  MaxDD {result.max_portfolio_drawdown*100:.1f}%")
    return results, time.perf_counter() - t0


def run_sweep(scenarios: list, budget: float, seed: int) -> tuple[dict, list[ScenarioResult]]:
    """
    Grid-search over (depth_fraction, kelly, max_market_fraction, max_order_age).

    Scoring metric: aggregate P&L weighted so that losses count 2× (risk-adjusted).
    This penalises parameter sets that profit on easy scenarios but blow up on shocks.
    """
    import itertools

    # Shallower depth → orders closer to mid → more fills in volatile markets
    depths = [0.15, 0.20, 0.30, 0.40]
    kellys = [0.20, 0.25, 0.30, 0.35]
    fracs  = [0.10, 0.15, 0.20]
    # Longer age → price has more time to reach our limit; shorter → faster cycling
    ages   = [14_400, 43_200, 86_400]   # 4h, 12h, 24h in seconds
    # Tighter stop → cut losses sooner; looser → let hedges ride longer
    stops  = [0.06, 0.10, 0.15, 1.0]   # 1.0 = disabled

    def score(results: list[ScenarioResult]) -> float:
        """Risk-adjusted aggregate P&L: losses penalised 2×."""
        total = 0.0
        for r in results:
            total += r.net_pnl if r.net_pnl >= 0 else r.net_pnl * 2
        return total

    best_score = float("-inf")
    best_params: dict = {}
    best_results: list[ScenarioResult] = []
    n_combos = len(depths) * len(kellys) * len(fracs) * len(ages) * len(stops)

    print(f"\n  Sweeping {n_combos} parameter combinations (risk-adjusted scoring) …")
    done = 0
    for depth, kelly, mf, age, stop in itertools.product(depths, kellys, fracs, ages, stops):
        cfg = _make_config(budget, depth, kelly, mf, age, stop)
        run_results = []
        for scenario in scenarios:
            bt = Backtester(cfg)
            run_results.append(bt.run_scenario(scenario, seed=seed))
        sc = score(run_results)
        done += 1
        if sc > best_score:
            best_score = sc
            best_params = {"depth": depth, "kelly": kelly, "mf": mf, "age": age, "stop": stop}
            best_results = run_results
        if done % 48 == 0:
            print(f"    {done}/{n_combos} … best score {best_score:+.2f}", flush=True)

    return best_params, best_results


def format_comparison(
    baseline: list[ScenarioResult],
    optimized: list[ScenarioResult],
    opt_params: dict,
) -> str:
    sep = "─" * 72
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║       BASELINE vs OPTIMISED COMPARISON                              ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        f"  Optimised params: depth={opt_params['depth']:.2f}  "
        f"kelly={opt_params['kelly']:.2f}  "
        f"max_market_frac={opt_params['mf']:.2f}  "
        f"max_order_age={opt_params['age']//3600}h  "
        f"hedge_stop={opt_params.get('stop', 0.12):.2f}",
        "",
        sep,
        f"  {'Scenario':<22} {'Base P&L':>10} {'Opt P&L':>10} {'Delta':>10} {'Imp%':>7}",
        "  " + "-" * 63,
    ]

    base_map = {r.scenario_name: r for r in baseline}
    opt_map  = {r.scenario_name: r for r in optimized}

    total_base = 0.0
    total_opt  = 0.0
    for name in [r.scenario_name for r in baseline]:
        b = base_map[name]
        o = opt_map.get(name)
        if o is None:
            continue
        delta = o.net_pnl - b.net_pnl
        imp = (delta / max(abs(b.net_pnl), 0.01)) * 100 if b.net_pnl != 0 else float("inf")
        imp_s = f"{imp:+.0f}%" if abs(imp) < 9999 else "new"
        delta_s = f"{delta:+.2f}"
        lines.append(
            f"  {name:<22} {b.net_pnl:>+10.2f} {o.net_pnl:>+10.2f} "
            f"{delta_s:>10} {imp_s:>7}"
        )
        total_base += b.net_pnl
        total_opt  += o.net_pnl

    total_delta = total_opt - total_base
    pct = (total_delta / max(abs(total_base), 0.01)) * 100
    lines += [
        "  " + "-" * 63,
        f"  {'TOTAL':<22} {total_base:>+10.2f} {total_opt:>+10.2f} "
        f"{total_delta:>+10.2f} {pct:>+6.0f}%",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi bot stress test")
    parser.add_argument("--budget",  type=float, default=1000.0)
    parser.add_argument("--seed",    type=int,   default=42)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--sweep",   action="store_true",
                        help="Grid-search parameters and show baseline vs best")
    parser.add_argument("--seeds",   type=int,   default=1,
                        help="Number of random seeds to average over (default 1)")
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

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s.name == args.scenario]
        if not scenarios:
            print(f"Unknown scenario '{args.scenario}'. Available:")
            for s in SCENARIOS:
                print(f"  {s.name:25s} – {s.description}")
            sys.exit(1)

    if args.sweep:
        # ── Parameter sweep ───────────────────────────────────────────────
        print(f"\nParameter sweep | budget=${args.budget:.0f} | seed={args.seed}")
        base_cfg = _make_config(args.budget, depth=0.40, kelly=0.25, mf=0.15, max_order_age=86_400)
        print("\nBaseline run:")
        base_results, base_t = _run_all(scenarios, base_cfg, args.seed, "base")

        best_params, opt_results = run_sweep(scenarios, args.budget, args.seed)

        print(f"\n  Best params found: {best_params}")
        print(format_comparison(base_results, opt_results, best_params))

        opt_cfg = _make_config(
            args.budget,
            depth=best_params["depth"],
            kelly=best_params["kelly"],
            mf=best_params["mf"],
            max_order_age=best_params["age"],
            hedge_stop_gap=best_params.get("stop", 0.12),
        )
        elapsed = base_t
        print(format_full_report(opt_results, elapsed))

    else:
        # ── Standard run – uses sweep-optimised defaults ──────────────────
        config = _make_config(args.budget, depth=0.40, kelly=0.20, mf=0.10, max_order_age=43_200)

        n_seeds = max(1, args.seeds)
        seeds   = [args.seed + i * 137 for i in range(n_seeds)]

        if n_seeds == 1:
            print(f"\nRunning {len(scenarios)} scenario(s) | budget=${args.budget:.0f} | seed={args.seed}")
            print("Please wait…\n")
            results, elapsed = _run_all(scenarios, config, args.seed, " ", args.verbose)
            print(format_full_report(results, elapsed))
        else:
            # Multi-seed: aggregate mean ± std across seeds
            import statistics
            print(f"\nMulti-seed simulation | {n_seeds} seeds | budget=${args.budget:.0f}")
            print(f"Params: kelly=0.20  mf=0.10  age=12h  stop=disabled\n")

            # Collect per-scenario P&L across all seeds
            scenario_pnls: dict[str, list[float]] = {s.name: [] for s in scenarios}
            all_elapsed = 0.0
            for seed_i, seed in enumerate(seeds):
                results, elapsed = _run_all(scenarios, config, seed, f"s{seed_i+1}", verbose=False)
                all_elapsed += elapsed
                for r in results:
                    scenario_pnls[r.scenario_name].append(r.net_pnl)

            sep = "─" * 72
            lines = [
                "",
                "╔══════════════════════════════════════════════════════════════════════╗",
                "║    KALSHI BOT — MULTI-SEED SIMULATION RESULTS                       ║",
                "╚══════════════════════════════════════════════════════════════════════╝",
                "",
                f"  Seeds         : {n_seeds}  (base={args.seed}, step=137)",
                f"  Budget        : ${args.budget:.0f}   kelly=0.20  mf=0.10  age=12h",
                f"  Scenarios     : {len(scenarios)}",
                "",
                sep,
                f"  {'Scenario':<22} {'Mean P&L':>10} {'Std Dev':>9} {'Min':>8} {'Max':>8} {'Win%':>6}",
                "  " + "-" * 66,
            ]

            all_means = []
            for scenario in scenarios:
                name = scenario.name
                pnls = scenario_pnls[name]
                mean = statistics.mean(pnls)
                std  = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
                mn   = min(pnls)
                mx   = max(pnls)
                wins = sum(1 for p in pnls if p > 0)
                win_pct = wins / len(pnls) * 100
                all_means.append(mean)
                sign = "+" if mean >= 0 else ""
                lines.append(
                    f"  {name:<22} {sign}{mean:>9.2f} {std:>9.2f} {mn:>8.2f} {mx:>8.2f} {win_pct:>5.0f}%"
                )

            total_mean = sum(all_means)
            lines += [
                "  " + "-" * 66,
                f"  {'AGGREGATE (sum)':<22} {'+' if total_mean >= 0 else ''}{total_mean:>9.2f}",
                "",
                sep,
                "  INTERPRETATION",
                sep,
                f"  • Avg per-scenario P&L across {n_seeds} random paths",
                "  • Win% = fraction of seeds where scenario was profitable",
                "  • High Std Dev relative to Mean → path-dependent; not robust",
                "  • Low Std Dev → consistent outcome regardless of price path",
                "",
                "  Strategy strengths:",
                "  • No scenario has unlimited downside (stop-loss + filter bound losses)",
                "  • 12-hour position cycling prevents runaway directional exposure",
                "",
                "  Strategy limitations (synthetic data):",
                "  • OU mean-reversion keeps prices near 50/50 → few fills in calm mkt",
                "  • Real Kalshi markets have news events that move prices sharply",
                "  • Black-swan type fills are the primary P&L source in this model",
                "",
            ]
            print("\n".join(lines))


if __name__ == "__main__":
    main()
