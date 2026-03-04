"""
Position sizing: Kelly criterion + budget allocation for Kalshi.

Kalshi trades in integer contract counts (not dollar notional).
Each contract costs `price` dollars and pays $1.00 at resolution.

Budget calculation:
  contracts = floor(budget_per_side / price)
  actual_cost = contracts * price

The market-making strategy places:
  - YES BUY orders at yes_price (slightly below mid)
  - NO  BUY orders at no_price  (slightly below no-mid = 1 − yes_mid)

Combined cost per pair: yes_price + no_price < $1.00 for guaranteed profit.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .client import MarketInfo
from .config import BotConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kelly fraction
# ---------------------------------------------------------------------------

def kelly_fraction(p: float, kelly_multiplier: float = 0.25) -> float:
    """
    Market-maker Kelly proxy: scales deployment with proximity to 50/50.

        f* = (1 − |2p − 1|) × kelly_multiplier

    Returns a fraction of available budget (0 ≤ f ≤ kelly_multiplier).
    """
    if p <= 0 or p >= 1:
        return 0.0
    balance = 1.0 - abs(2 * p - 1)
    return balance * kelly_multiplier


# ---------------------------------------------------------------------------
# Sizing result
# ---------------------------------------------------------------------------

@dataclass
class SizingResult:
    yes_price: float          # recommended YES order price (prob 0-1)
    no_price: float           # recommended NO order price (prob 0-1)
    contracts_per_level: int  # number of contracts per order
    num_levels: int
    budget_allocated: float   # total USD committed (2 sides × levels × contracts × price)
    expected_profit_per_pair: float  # 1 - (yes_price + no_price), if both fill


# ---------------------------------------------------------------------------
# Main sizing function
# ---------------------------------------------------------------------------

def size_position(
    market: MarketInfo,
    available_budget: float,
    config: BotConfig,
) -> SizingResult:
    """
    Compute order prices and contract counts for a Kalshi market.

    Strategy:
      1. Kelly fraction determines budget allocation.
      2. Budget split evenly across `risk.order_levels` levels per side.
      3. YES price = mid − depth;  NO price = (1 − mid) − depth
      4. Contract count = floor(budget_per_level / price)
    """
    risk = config.risk
    sc = config.scoring

    mid = market.mid_price
    levels = risk.order_levels

    # 1. Kelly allocation
    k = kelly_fraction(mid, risk.kelly_multiplier)
    alloc_fraction = min(k, risk.max_market_fraction)
    budget_for_market = available_budget * alloc_fraction

    # 2. Budget per level per side
    budget_per_level = budget_for_market / max(2 * levels, 1)

    # 3. Order prices (depth inside the spread from mid)
    spread = market.spread
    v = max(spread, sc.default_v)
    depth = v * sc.order_depth_fraction

    yes_price = round(max(mid - depth, 0.01), 4)
    no_mid = 1.0 - mid
    no_price = round(max(no_mid - depth, 0.01), 4)

    # Safety: combined cost must be ≤ max_fill_cost
    combined = yes_price + no_price
    if combined > risk.max_fill_cost:
        excess = (combined - risk.max_fill_cost + 0.001) / 2
        yes_price = round(yes_price - excess, 4)
        no_price = round(no_price - excess, 4)
        logger.debug(
            "Price adjustment: yes=%.4f no=%.4f combined=%.4f",
            yes_price, no_price, yes_price + no_price,
        )

    # 4. Contract count: use the cheaper side to determine count
    #    (both sides use the same count for balanced hedging)
    ref_price = max(yes_price, no_price, 0.01)
    contracts = max(int(budget_per_level / ref_price), risk.min_order_contracts)

    actual_budget = 2 * levels * contracts * ref_price

    expected_profit = max(0.0, 1.0 - (yes_price + no_price))

    logger.debug(
        "Sizing [%s]: kelly=%.3f alloc=$%.1f yes=%.4f no=%.4f × %d contracts",
        market.ticker, k, actual_budget, yes_price, no_price, contracts,
    )

    return SizingResult(
        yes_price=yes_price,
        no_price=no_price,
        contracts_per_level=contracts,
        num_levels=levels,
        budget_allocated=actual_budget,
        expected_profit_per_pair=expected_profit,
    )


# ---------------------------------------------------------------------------
# Budget tracker
# ---------------------------------------------------------------------------

class BudgetTracker:
    """Tracks deployed budget across open positions."""

    def __init__(self, total_budget: float) -> None:
        self.total = total_budget
        self._deployed: dict[str, float] = {}  # ticker → USD deployed

    @property
    def available(self) -> float:
        return self.total - sum(self._deployed.values())

    def allocate(self, ticker: str, amount: float) -> None:
        self._deployed[ticker] = amount

    def release(self, ticker: str) -> None:
        self._deployed.pop(ticker, None)

    def deployed_in(self, ticker: str) -> float:
        return self._deployed.get(ticker, 0.0)

    def summary(self) -> str:
        deployed = sum(self._deployed.values())
        return (
            f"Budget: total=${self.total:.2f} | "
            f"deployed=${deployed:.2f} | "
            f"available=${self.available:.2f}"
        )
