"""
Profit estimation for the Kalshi market-making strategy.

Unlike Polymarket, Kalshi does not have a public liquidity-reward programme
(no LP scoring function). Profit comes from:

  1. Spread capture – when both sides (YES buy + NO buy) fill at a combined
     cost < $1.00, the $1.00 payout at resolution is risk-free profit.

  2. Directional edge – if our YES or NO order fills at a favourable price
     and the market moves our way before resolution.

This module focuses on the spread-capture (market-making) scenario:

  P&L = $1.00 − (p_yes + p_no)

Three scenarios at any point in time:
  1. Neither order fills   → no P&L yet; continue collecting optionality
  2. One side fills        → must hedge the other side immediately
  3. Both sides fill       → locked-in profit = 1 − (p_yes + p_no)

All prices in probability units (0.0 – 1.0).
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# P&L scenario analysis
# ---------------------------------------------------------------------------

@dataclass
class ScenarioPnL:
    """Profit/loss breakdown for the three fill scenarios."""

    # Scenario 1: neither order filled
    neither_filled_note: str

    # Scenario 2: one side filled – must hedge immediately
    one_filled_net_pnl: float        # profit at resolution
    one_filled_hedge_cost: float     # total outlay YES + NO
    one_filled_is_profitable: bool   # combined ≤ max_fill_cost

    # Scenario 3: both sides filled
    both_filled_net_pnl: float       # 1.00 − (p_yes + p_no)
    both_filled_is_profitable: bool  # net pnl > 0


def compute_scenario_pnl(
    p_yes: float,
    p_no: float,
    max_fill_cost: float = 1.02,
) -> ScenarioPnL:
    """
    Compute expected outcomes for all three scenarios.

    Parameters
    ----------
    p_yes         : Price paid for a YES contract (0-1)
    p_no          : Price paid for a NO contract  (0-1)
    max_fill_cost : Maximum acceptable p_yes + p_no for a profitable hedge
    """
    combined = p_yes + p_no
    pnl_both = 1.0 - combined

    return ScenarioPnL(
        neither_filled_note="Orders resting; waiting for fills.",
        one_filled_net_pnl=1.0 - combined,
        one_filled_hedge_cost=combined,
        one_filled_is_profitable=combined <= max_fill_cost,
        both_filled_net_pnl=pnl_both,
        both_filled_is_profitable=pnl_both > 0,
    )


# ---------------------------------------------------------------------------
# Spread / ladder helpers  (reused from market-making logic)
# ---------------------------------------------------------------------------

@dataclass
class LadderSpec:
    """Describes a set of orders placed at multiple price levels."""
    levels: int
    base_depth: float       # distance of first level from mid (prob units)
    level_gap: float        # gap between successive levels
    size_per_level: float   # USD notional per order level

    def depths(self) -> list[float]:
        return [self.base_depth + i * self.level_gap for i in range(self.levels)]


def expected_spread_capture(
    p_yes: float,
    p_no: float,
) -> float:
    """
    Maximum profit if both sides fill (spread capture scenario).

    Returns
    -------
    float : dollars profit per dollar-pair at resolution
    """
    return max(0.0, 1.0 - (p_yes + p_no))


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------

def format_scenario_summary(pnl: ScenarioPnL, p_yes: float, p_no: float) -> str:
    lines = [
        f"  YES price : {p_yes:.4f}  |  NO price : {p_no:.4f}",
        f"  Combined  : {p_yes + p_no:.4f}  (max allowed {1.02:.2f})",
        "",
        "  Scenario 1 – Neither fills:",
        f"    {pnl.neither_filled_note}",
        "",
        "  Scenario 2 – One side fills (hedge immediately):",
        f"    Hedge cost           : ${pnl.one_filled_hedge_cost:.4f}",
        f"    Net PnL at resolution: ${pnl.one_filled_net_pnl:.4f}",
        f"    Profitable?          : {pnl.one_filled_is_profitable}",
        "",
        "  Scenario 3 – Both sides filled (fully hedged):",
        f"    Net PnL              : ${pnl.both_filled_net_pnl:.4f}",
        f"    Profitable?          : {pnl.both_filled_is_profitable}",
    ]
    return "\n".join(lines)
