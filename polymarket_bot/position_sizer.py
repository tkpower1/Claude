"""
Position sizing: Kelly criterion + budget allocation.

Paper §6:

    Kelly fraction:
        f* = (p·b - q) / b

    where:
        p  = probability of winning (we model as the YES mid-price)
        q  = 1 - p
        b  = net odds (payout per dollar risked = 1/p - 1 for binary markets)

    Full Kelly is aggressive; we use a configurable fraction (default 0.25).

    Budget distribution across markets:
        size_per_order = (budget_for_market) / (2 · levels · price)

    The factor of 2 comes from placing orders on both sides (YES and NO).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import BotConfig, RiskParams
from .client import MarketInfo
from .rewards import LadderSpec, order_score, ladder_total_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kelly criterion
# ---------------------------------------------------------------------------

def kelly_fraction(p: float, kelly_multiplier: float = 0.25) -> float:
    """
    Position-sizing fraction for the LP / hedge strategy, styled after Kelly.

    In an efficient market the classical Kelly formula gives zero edge because
    the price already equals the true probability.  For a *market-maker* the
    edge comes from the reward programme and the bid-ask spread we capture –
    not from directional forecasting.  We therefore use a balance-based proxy:

        f* = balance(p) × kelly_multiplier

    where:

        balance(p) = 1 − |2p − 1|

    This equals 1 at p = 0.50 (most balanced, lowest directional risk, easiest
    to hedge) and 0 at p = 0 or 1 (fully one-sided, impossible to profit).
    It scales the deployment fraction smoothly with proximity to 50/50.

    Parameters
    ----------
    p               : YES mid-price / probability (0 < p < 1)
    kelly_multiplier: maximum fraction to deploy in any single market
                      (default 0.25 = cap at 25 % of available budget)

    Returns
    -------
    float : fraction of available budget to deploy (0 ≤ f ≤ kelly_multiplier)
    """
    if p <= 0 or p >= 1:
        return 0.0

    balance = 1.0 - abs(2 * p - 1)   # 1 at p=0.5, 0 at extremes
    return balance * kelly_multiplier


# ---------------------------------------------------------------------------
# Per-market budget allocation
# ---------------------------------------------------------------------------

@dataclass
class SizingResult:
    """Output of the position sizer for one market."""
    yes_price: float          # recommended YES order price
    no_price: float           # recommended NO order price
    size_per_level: float     # USDC notional per order
    num_levels: int           # ladder depth
    ladder: LadderSpec
    budget_allocated: float   # total USDC committed (2 * levels * size_per_level)
    expected_daily_reward: float  # USDC


def size_position(
    market: MarketInfo,
    available_budget: float,
    config: BotConfig,
) -> SizingResult:
    """
    Compute order prices and sizes for a market.

    Strategy:
      1. Use Kelly to determine how much of the total budget to allocate.
      2. Spread that allocation across `risk.order_levels` levels per side.
      3. YES price = mid - depth;  NO price = (1 - mid) - depth
         (both inside the reward window, symmetric around their respective mids).
      4. Verify combined cost ≤ max_fill_cost.

    Parameters
    ----------
    market          : MarketInfo with current book state
    available_budget: USDC we can deploy right now
    config          : BotConfig

    Returns
    -------
    SizingResult
    """
    risk = config.risk
    sc = config.scoring

    mid = market.mid_price
    v = market.max_spread
    b = market.multiplier
    levels = risk.order_levels

    # 1. Kelly allocation
    k = kelly_fraction(mid, risk.kelly_multiplier)
    max_fraction = risk.max_market_fraction
    alloc_fraction = min(k, max_fraction)
    budget_for_market = available_budget * alloc_fraction

    # 2. Per-level size  (2 sides × levels × price ≈ budget)
    #    Each order notional: budget / (2 * levels)
    size_per_level = budget_for_market / max(2 * levels, 1)
    size_per_level = max(size_per_level, 1.0)    # Polymarket minimum ~$1

    # 3. Order prices
    #    We place the first level at (v * depth_fraction) from mid.
    depth = v * sc.order_depth_fraction

    # YES: we BUY YES at a price slightly below mid
    yes_price = round(max(mid - depth, 0.01), 4)

    # NO: we BUY NO at a price slightly below (1 - mid) = NO mid
    no_mid = 1.0 - mid
    no_price = round(max(no_mid - depth, 0.01), 4)

    # 4. Safety: combined cost check
    combined = yes_price + no_price
    max_cost = risk.max_fill_cost
    if combined > max_cost:
        # Squeeze prices down proportionally to fit
        excess = combined - max_cost + 0.001
        yes_price = round(yes_price - excess / 2, 4)
        no_price = round(no_price - excess / 2, 4)
        logger.debug(
            "Adjusted prices: yes=%.4f no=%.4f combined=%.4f",
            yes_price, no_price, yes_price + no_price,
        )

    # 5. Ladder spec
    ladder = LadderSpec(
        levels=levels,
        base_depth=depth,
        level_gap=risk.level_gap,
        size_per_level=size_per_level,
    )

    # 6. Estimate daily rewards
    total_score = ladder_total_score(ladder, v, b, sides=2)
    # Rough estimate: assume we have 5 % of the total competing score
    est_total_score = total_score * 20
    daily_reward = (total_score / max(est_total_score, 1e-9)) * market.reward_rate

    budget_allocated = 2 * levels * size_per_level

    logger.debug(
        "Sizing [%s]: kelly=%.3f alloc=%.1f USDC yes=%.4f no=%.4f levels=%d reward≈%.4f/day",
        market.question[:40], k, budget_allocated, yes_price, no_price, levels, daily_reward,
    )

    return SizingResult(
        yes_price=yes_price,
        no_price=no_price,
        size_per_level=size_per_level,
        num_levels=levels,
        ladder=ladder,
        budget_allocated=budget_allocated,
        expected_daily_reward=daily_reward,
    )


# ---------------------------------------------------------------------------
# Portfolio budget tracker
# ---------------------------------------------------------------------------

class BudgetTracker:
    """Tracks how much budget is deployed across all open positions."""

    def __init__(self, total_budget: float) -> None:
        self.total = total_budget
        self._deployed: dict[str, float] = {}   # condition_id → USDC deployed

    @property
    def available(self) -> float:
        return self.total - sum(self._deployed.values())

    def allocate(self, condition_id: str, amount: float) -> None:
        self._deployed[condition_id] = amount

    def release(self, condition_id: str) -> None:
        self._deployed.pop(condition_id, None)

    def deployed_in(self, condition_id: str) -> float:
        return self._deployed.get(condition_id, 0.0)

    def summary(self) -> str:
        deployed = sum(self._deployed.values())
        return (
            f"Budget: total=${self.total:.2f} | "
            f"deployed=${deployed:.2f} | "
            f"available=${self.available:.2f}"
        )
