"""
Scoring function and reward estimation.

Paper §3.1:

    S(s) = ((v - s) / v)² · b

Where:
    s  – distance of our order from the mid price (probability units)
    v  – maximum spread window (probability units); orders outside earn 0
    b  – market multiplier (from API)

Our reward share:

    R_i = (S_i / Σ_j S_j) · P_daily

Where P_daily is the total daily reward pool for the market (USDC).

Expected daily income:

    income ≈ R_i · (size / market_total_liquidity)

This module also computes net P&L for the three fill scenarios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .config import ScoringParams


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def order_score(s: float, v: float, b: float = 1.0) -> float:
    """
    Compute the score S(s) for a single order.

    Parameters
    ----------
    s : float
        Absolute distance of the order from the current mid price.
        Must be in [0, v]; orders at s >= v score zero.
    v : float
        Maximum spread from mid for non-zero score (the reward window).
    b : float
        Market multiplier (default 1.0).

    Returns
    -------
    float
        Score value ≥ 0. Higher is better.
    """
    if v <= 0 or s >= v:
        return 0.0
    return ((v - s) / v) ** 2 * b


def optimal_order_depth(v: float, scoring: ScoringParams) -> float:
    """
    Return the order distance s* that maximises the score-to-spread trade-off.

    Analytically, dS/ds = -2(v-s)/v² · b, so the score always increases as
    s → 0 (closer to mid). However, placing too close to mid increases fill
    risk and reduces the bid-ask spread we capture. We therefore sit at a
    configurable fraction of v.
    """
    return v * scoring.order_depth_fraction


def estimate_reward_share(
    our_score: float,
    total_competing_score: float,
    daily_pool_usdc: float,
) -> float:
    """
    Estimate our share of the daily reward pool.

    Parameters
    ----------
    our_score           : sum of S(s) across all our orders in this market
    total_competing_score: sum of S(s) for ALL makers including us
    daily_pool_usdc     : total USDC rewards issued per day for this market

    Returns
    -------
    float : estimated daily USDC income from rewards
    """
    if total_competing_score <= 0:
        return 0.0
    share = our_score / total_competing_score
    return share * daily_pool_usdc


# ---------------------------------------------------------------------------
# Multi-level ladder scoring
# ---------------------------------------------------------------------------

@dataclass
class LadderSpec:
    """Describes a set of orders placed at multiple levels around mid."""
    levels: int          # number of price levels per side
    base_depth: float    # distance of first level from mid (probability units)
    level_gap: float     # gap between successive levels
    size_per_level: float  # USDC notional per order

    def depths(self) -> list[float]:
        """Return list of distances from mid for each level."""
        return [self.base_depth + i * self.level_gap for i in range(self.levels)]


def ladder_total_score(
    ladder: LadderSpec,
    v: float,
    b: float = 1.0,
    sides: int = 2,      # 1 = one side only, 2 = YES + NO
) -> float:
    """
    Compute total score for a full ladder (both sides).

    Parameters
    ----------
    ladder : LadderSpec
    v      : reward window (probability units)
    b      : market multiplier
    sides  : 2 for YES+NO (symmetric), 1 for one-sided hedge

    Returns
    -------
    float : total S across all orders placed
    """
    total = 0.0
    for depth in ladder.depths():
        s = order_score(depth, v, b)
        total += s * sides  # each depth level has an order on each side
    return total


# ---------------------------------------------------------------------------
# P&L scenarios  (paper §4 – the three cases)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioPnL:
    """Profit/loss breakdown for the three fill scenarios."""

    # Scenario 1: neither order filled – net income from rewards
    neither_filled_daily_income: float    # USDC / day from liquidity rewards

    # Scenario 2: one side filled – immediate hedge placed on opposite side
    one_filled_net_pnl: float             # profit guaranteed at resolution
    one_filled_hedge_cost: float          # cost to place the hedge
    one_filled_is_profitable: bool        # YES + NO ≤ max_fill_cost

    # Scenario 3: both sides filled – fully hedged, deterministic profit
    both_filled_net_pnl: float            # 1.00 - (p_yes + p_no)
    both_filled_is_profitable: bool       # net pnl > 0


def compute_scenario_pnl(
    p_yes: float,          # price paid for YES share
    p_no: float,           # price paid for NO share
    rewards_per_day: float,
    max_fill_cost: float = 1.02,
) -> ScenarioPnL:
    """
    Compute expected outcomes for all three scenarios.

    Parameters
    ----------
    p_yes           : Price of the YES limit order (probability units, e.g. 0.48)
    p_no            : Price of the NO limit order  (probability units, e.g. 0.50)
    rewards_per_day : Estimated daily USDC income from holding both orders open
    max_fill_cost   : Maximum acceptable p_yes + p_no for a profitable hedge

    Returns
    -------
    ScenarioPnL
    """
    combined_cost = p_yes + p_no
    pnl_if_both = 1.0 - combined_cost   # one side pays $1, other pays $0

    # Scenario 2: one side fills, hedge the other immediately.
    # If YES fills at p_yes, we immediately buy NO at market.
    # Best-case NO fill price = p_no (our standing order).
    # Worst-case: we buy NO at the ask, which could be slightly higher.
    # We use our posted p_no as the conservative estimate.
    hedge_cost = combined_cost           # total outlay for YES + NO
    one_filled_pnl = 1.0 - hedge_cost   # guaranteed payout at resolution

    return ScenarioPnL(
        neither_filled_daily_income=rewards_per_day,
        one_filled_net_pnl=one_filled_pnl,
        one_filled_hedge_cost=hedge_cost,
        one_filled_is_profitable=combined_cost <= max_fill_cost,
        both_filled_net_pnl=pnl_if_both,
        both_filled_is_profitable=pnl_if_both > 0,
    )


# ---------------------------------------------------------------------------
# Break-even / minimum reward analysis
# ---------------------------------------------------------------------------

def min_reward_to_break_even(
    p_yes: float,
    p_no: float,
    fill_probability_per_day: float,
    max_fill_cost: float = 1.02,
) -> float:
    """
    Minimum daily reward income (USDC per $ deployed) needed to make the
    strategy break even, accounting for the probability of an unfavourable fill.

    If both orders can fill and combined cost > max_fill_cost, there is a
    potential loss. The reward stream must exceed the expected loss rate.

    Parameters
    ----------
    p_yes, p_no         : posted order prices
    fill_probability_per_day : estimated daily probability that one side fills
    max_fill_cost       : threshold above which a fill would be loss-making

    Returns
    -------
    float : minimum required daily reward rate (fraction, not USDC amount)
    """
    combined = p_yes + p_no
    if combined <= max_fill_cost:
        return 0.0   # always profitable regardless – no minimum reward needed

    loss_if_filled = combined - max_fill_cost   # potential loss per $ exposed
    # Expected daily loss = loss_per_fill * P(fill)
    return loss_if_filled * fill_probability_per_day


# ---------------------------------------------------------------------------
# Quick summary helper
# ---------------------------------------------------------------------------

def format_scenario_summary(pnl: ScenarioPnL, p_yes: float, p_no: float) -> str:
    lines = [
        f"  YES price : {p_yes:.4f}  |  NO price : {p_no:.4f}",
        f"  Combined  : {p_yes + p_no:.4f}  (max allowed {1.02:.2f})",
        "",
        f"  Scenario 1 – Neither fills:",
        f"    Daily reward income  : ${pnl.neither_filled_daily_income:.4f}",
        "",
        f"  Scenario 2 – One side fills (hedge immediately):",
        f"    Hedge cost           : ${pnl.one_filled_hedge_cost:.4f}",
        f"    Net PnL at resolution: ${pnl.one_filled_net_pnl:.4f}",
        f"    Profitable?          : {pnl.one_filled_is_profitable}",
        "",
        f"  Scenario 3 – Both sides filled (fully hedged):",
        f"    Net PnL              : ${pnl.both_filled_net_pnl:.4f}",
        f"    Profitable?          : {pnl.both_filled_is_profitable}",
    ]
    return "\n".join(lines)
