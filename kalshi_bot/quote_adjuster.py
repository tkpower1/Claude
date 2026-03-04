"""
Order-flow-aware quote adjustment (LMSR-inspired).

The article's insight
──────────────────────
LMSR prices emerge from a softmax over cumulative quantities:

    pᵢ = e^(qᵢ/b) / Σⱼ e^(qⱼ/b)

When qᵢ is large (many YES contracts bought), p_YES rises.
A market maker quoting a fixed spread around mid ignores this signal.

Applied to limit-order market making
──────────────────────────────────────
We don't run an AMM, but we can use the SAME intuition:

  Order-flow imbalance (OFI) = (yes_bid_depth - no_bid_depth) /
                                (yes_bid_depth + no_bid_depth)

  OFI ∈ [-1, 1]:
    +1  → all buying YES  → YES is in demand, adverse selection risk for YES MM
    -1  → all buying NO   → NO is in demand, adverse selection risk for NO MM
     0  → balanced        → neutral, no adjustment needed

Adjustment: widen the quote on the side under buying pressure.

    if OFI > 0 (YES pressure):
        yes_price -= ofi_adjustment   # move YES price further inside (worse for takers)
        no_price  += ofi_adjustment   # keep NO price unchanged or tighten

    if OFI < 0 (NO pressure):
        no_price  -= ofi_adjustment
        yes_price += ofi_adjustment

ofi_adjustment = ofi_sensitivity × |OFI| × spread

This is a linearized version of the LMSR price update:
  Δp ≈ (1/b) · Δq · p · (1-p)   (first-order Taylor of softmax)

Capped at half the spread so we never push prices through mid.

Usage in position_sizer.py
───────────────────────────
    from .quote_adjuster import adjust_for_order_flow
    yes_adj, no_adj = adjust_for_order_flow(order_book, spread, config)
    yes_price -= yes_adj
    no_price  -= no_adj
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def order_flow_imbalance(
    yes_bid_depth: float,   # USD or contracts resting on YES bid side
    no_bid_depth: float,    # USD or contracts resting on NO bid side
) -> float:
    """
    Compute order-flow imbalance OFI ∈ [-1, +1].

      +1 → all volume on YES bid (buyers pushing YES price up)
      -1 → all volume on NO bid
       0 → balanced

    Returns 0.0 if total depth is zero.
    """
    total = yes_bid_depth + no_bid_depth
    if total <= 0:
        return 0.0
    return (yes_bid_depth - no_bid_depth) / total


def adjust_for_order_flow(
    yes_bid_depth: float,
    no_bid_depth: float,
    spread: float,
    ofi_sensitivity: float = 0.25,
    max_adjustment: Optional[float] = None,
) -> tuple[float, float]:
    """
    Compute (yes_price_reduction, no_price_reduction) based on order-flow.

    A positive reduction means moving the price FURTHER from mid (worse fill
    probability, but better protection against adverse selection).

    Parameters
    ----------
    yes_bid_depth   : depth at or near best YES bid (e.g. sum of top 3 levels)
    no_bid_depth    : depth at or near best NO bid
    spread          : current bid-ask spread (probability units)
    ofi_sensitivity : fraction of spread to adjust per unit of OFI (default 0.25)
    max_adjustment  : cap per-side (default: spread / 4)

    Returns
    -------
    (yes_adj, no_adj) — reduce yes_price by yes_adj, no_price by no_adj.
    Both are non-negative. One will always be 0 (the unexposed side).
    """
    if max_adjustment is None:
        max_adjustment = spread / 4.0

    ofi = order_flow_imbalance(yes_bid_depth, no_bid_depth)
    raw_adj = abs(ofi) * ofi_sensitivity * spread
    adj = min(raw_adj, max_adjustment)

    if ofi > 0:
        # YES buying pressure → widen YES quote (reduce our YES bid price)
        yes_adj = adj
        no_adj  = 0.0
    elif ofi < 0:
        # NO buying pressure → widen NO quote
        yes_adj = 0.0
        no_adj  = adj
    else:
        yes_adj = no_adj = 0.0

    if adj > 0:
        logger.debug(
            "OFI=%.3f → yes_adj=%.4f no_adj=%.4f (spread=%.4f)",
            ofi, yes_adj, no_adj, spread,
        )

    return yes_adj, no_adj


def extract_depth_from_order_book(order_book) -> tuple[float, float]:
    """
    Extract approximate YES and NO bid depths from a Kalshi OrderBook.

    OrderBook.yes_bids = [(price, count), ...] descending
    We sum the top 3 levels for a robust signal.
    """
    yes_depth = sum(price * count for price, count in (order_book.yes_bids or [])[:3])
    # NO bids are the complement: yes_ask side represents NO bid interest
    no_depth  = sum(price * count for price, count in (order_book.yes_asks or [])[:3])
    return yes_depth, no_depth
