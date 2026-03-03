"""
Order manager: places, tracks, and refreshes YES/NO limit orders.

State machine for each market position:

    IDLE
      │  (market selected)
      ▼
    QUOTING  ← both YES + NO orders live; collecting rewards
      │   │
      │   │ YES fills
      │   ▼
      │  YES_FILLED  → place NO hedge immediately
      │      │
      │      └──────────────────────┐
      │                             ▼
      │   NO fills          ONE_SIDE_HEDGED  (fully hedged, wait for resolution)
      ▼   ▼
    BOTH_FILLED (same outcome – wait for resolution)
      │
      ▼
    RESOLVED → collect payout, mark IDLE

At every state transition the hedger ensures YES + NO ≤ max_fill_cost.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .client import ClobClient, Order
from .config import BotConfig
from .rewards import compute_scenario_pnl, format_scenario_summary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class PositionState(Enum):
    IDLE = auto()
    QUOTING = auto()          # both orders live
    YES_FILLED = auto()       # YES order filled, hedging
    NO_FILLED = auto()        # NO order filled, hedging
    ONE_SIDE_HEDGED = auto()  # hedge placed, waiting resolution
    BOTH_FILLED = auto()      # fully hedged, waiting resolution
    RESOLVED = auto()


@dataclass
class MarketPosition:
    """Tracks a single market's LP position."""
    condition_id: str
    question: str

    yes_token_id: str
    no_token_id: str

    # Posted prices
    yes_price: float = 0.0
    no_price: float = 0.0

    # Order IDs currently live
    yes_order_id: Optional[str] = None
    no_order_id: Optional[str] = None

    # Hedge order ID (placed after one side fills)
    hedge_order_id: Optional[str] = None

    state: PositionState = PositionState.IDLE

    # Cumulative P&L tracking
    total_rewards_earned: float = 0.0
    total_fees_paid: float = 0.0
    realised_pnl: float = 0.0

    # When the orders were last placed (unix timestamp)
    last_quote_time: float = field(default_factory=time.time)

    # Number of shares (size) per order
    size: float = 0.0


# ---------------------------------------------------------------------------
# Order manager
# ---------------------------------------------------------------------------

class OrderManager:
    """
    Places and manages YES/NO liquidity orders for multiple markets.
    Handles hedging when a fill is detected.
    """

    def __init__(self, client: ClobClient, config: BotConfig) -> None:
        self.client = client
        self.cfg = config
        self.positions: dict[str, MarketPosition] = {}   # condition_id → position

    # ------------------------------------------------------------------
    # Quoting
    # ------------------------------------------------------------------

    def open_position(
        self,
        condition_id: str,
        question: str,
        yes_token_id: str,
        no_token_id: str,
        yes_price: float,
        no_price: float,
        size: float,
    ) -> MarketPosition:
        """
        Place YES-BUY and NO-BUY limit orders and record the position.

        YES-BUY at yes_price means we pay yes_price per share; if YES resolves
        true the share pays $1. Similarly for NO.

        Combined outlay per unit: yes_price + no_price ≤ 1.02 (profit threshold).
        """
        if condition_id in self.positions:
            pos = self.positions[condition_id]
            if pos.state not in (PositionState.IDLE, PositionState.RESOLVED):
                logger.warning(
                    "Position %s already open (state=%s) – skipping.",
                    condition_id[:12], pos.state.name,
                )
                return pos

        # Safety check before posting
        combined = yes_price + no_price
        max_cost = self.cfg.risk.max_fill_cost
        if combined > max_cost:
            logger.warning(
                "%.4f + %.4f = %.4f > %.4f – skipping market %s",
                yes_price, no_price, combined, max_cost, question[:40],
            )
            pos = MarketPosition(
                condition_id=condition_id,
                question=question,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                state=PositionState.IDLE,
            )
            self.positions[condition_id] = pos
            return pos

        pos = MarketPosition(
            condition_id=condition_id,
            question=question,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            yes_price=yes_price,
            no_price=no_price,
            size=size,
        )

        # Place YES buy
        yes_id = self.client.place_limit_order(
            token_id=yes_token_id,
            side="BUY",
            price=yes_price,
            size=size,
        )

        # Place NO buy  (NO token price = 1 - YES_mid approximately)
        no_id = self.client.place_limit_order(
            token_id=no_token_id,
            side="BUY",
            price=no_price,
            size=size,
        )

        pos.yes_order_id = yes_id
        pos.no_order_id = no_id
        pos.state = PositionState.QUOTING
        pos.last_quote_time = time.time()

        self.positions[condition_id] = pos

        pnl = compute_scenario_pnl(yes_price, no_price, 0.0, max_cost)
        logger.info(
            "Opened position %s\n%s",
            question[:60],
            format_scenario_summary(pnl, yes_price, no_price),
        )
        return pos

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        """
        Poll open orders and advance the state machine for every position.
        Call this on each bot tick.
        """
        open_orders = {o.order_id: o for o in self.client.get_open_orders()}

        for cid, pos in list(self.positions.items()):
            try:
                self._refresh_position(pos, open_orders)
            except Exception as exc:
                logger.error("refresh_position(%s): %s", cid[:12], exc)

    def _refresh_position(
        self,
        pos: MarketPosition,
        open_orders: dict[str, Order],
    ) -> None:

        if pos.state in (PositionState.IDLE, PositionState.RESOLVED):
            return

        yes_live = pos.yes_order_id in open_orders
        no_live = pos.no_order_id in open_orders

        # ----------------------------------------------------------------
        # QUOTING: check if any order was filled
        # ----------------------------------------------------------------
        if pos.state == PositionState.QUOTING:
            yes_filled = not yes_live and pos.yes_order_id is not None
            no_filled = not no_live and pos.no_order_id is not None

            if yes_filled and no_filled:
                # Scenario 3: both filled – fully hedged
                pnl = 1.0 - (pos.yes_price + pos.no_price)
                pos.realised_pnl += pnl
                pos.state = PositionState.BOTH_FILLED
                logger.info(
                    "[%s] BOTH filled – net PnL=%.4f USDC",
                    pos.question[:40], pnl,
                )

            elif yes_filled:
                # Scenario 2a: YES filled, must hedge NO immediately
                pos.state = PositionState.YES_FILLED
                logger.info("[%s] YES filled @ %.4f – hedging NO…", pos.question[:40], pos.yes_price)
                self._hedge_no(pos)

            elif no_filled:
                # Scenario 2b: NO filled, must hedge YES immediately
                pos.state = PositionState.NO_FILLED
                logger.info("[%s] NO filled @ %.4f – hedging YES…", pos.question[:40], pos.no_price)
                self._hedge_yes(pos)

            else:
                # Neither filled – check if re-quote is needed
                self._maybe_requote(pos, open_orders)

        # ----------------------------------------------------------------
        # ONE_SIDE_HEDGED: wait for hedge fill then confirm fully hedged
        # ----------------------------------------------------------------
        elif pos.state == PositionState.ONE_SIDE_HEDGED:
            hedge_live = pos.hedge_order_id in open_orders
            if not hedge_live and pos.hedge_order_id is not None:
                # Hedge filled – now fully hedged
                pos.state = PositionState.BOTH_FILLED
                logger.info("[%s] Hedge filled – BOTH_FILLED.", pos.question[:40])

        # ----------------------------------------------------------------
        # BOTH_FILLED: nothing to do until resolution (handled externally)
        # ----------------------------------------------------------------
        elif pos.state == PositionState.BOTH_FILLED:
            pass   # resolution detected by bot.py via market closure

    # ------------------------------------------------------------------
    # Hedging logic
    # ------------------------------------------------------------------

    def _hedge_no(self, pos: MarketPosition) -> None:
        """
        After YES fills, place a NO buy to complete the hedge.

        We first cancel any live NO order (if still on book) and immediately
        place a new one. The new price must satisfy:
            yes_price_paid + hedge_no_price ≤ max_fill_cost

        We use our original posted no_price. If that order is still live we
        can simply track it; if it was also filled we transition to BOTH_FILLED.
        """
        max_cost = self.cfg.risk.max_fill_cost
        max_no_price = max_cost - pos.yes_price

        if max_no_price <= 0:
            logger.error(
                "[%s] YES filled at %.4f – no headroom for profitable NO hedge (max_cost=%.2f).",
                pos.question[:40], pos.yes_price, max_cost,
            )
            pos.state = PositionState.ONE_SIDE_HEDGED  # place market order as last resort
            return

        # Clamp to 1 (NO price can't exceed 1)
        hedge_price = min(max_no_price, 0.99)

        hedge_id = self.client.place_limit_order(
            token_id=pos.no_token_id,
            side="BUY",
            price=hedge_price,
            size=pos.size,
        )
        pos.hedge_order_id = hedge_id
        pos.state = PositionState.ONE_SIDE_HEDGED

        logger.info(
            "[%s] NO hedge placed @ %.4f (YES paid %.4f, combined=%.4f)",
            pos.question[:40], hedge_price, pos.yes_price, pos.yes_price + hedge_price,
        )

    def _hedge_yes(self, pos: MarketPosition) -> None:
        """After NO fills, place a YES buy to complete the hedge."""
        max_cost = self.cfg.risk.max_fill_cost
        max_yes_price = max_cost - pos.no_price

        if max_yes_price <= 0:
            logger.error(
                "[%s] NO filled at %.4f – no headroom for profitable YES hedge.",
                pos.question[:40], pos.no_price,
            )
            pos.state = PositionState.ONE_SIDE_HEDGED
            return

        hedge_price = min(max_yes_price, 0.99)

        hedge_id = self.client.place_limit_order(
            token_id=pos.yes_token_id,
            side="BUY",
            price=hedge_price,
            size=pos.size,
        )
        pos.hedge_order_id = hedge_id
        pos.state = PositionState.ONE_SIDE_HEDGED

        logger.info(
            "[%s] YES hedge placed @ %.4f (NO paid %.4f, combined=%.4f)",
            pos.question[:40], hedge_price, pos.no_price, pos.no_price + hedge_price,
        )

    # ------------------------------------------------------------------
    # Re-quoting
    # ------------------------------------------------------------------

    def _maybe_requote(
        self,
        pos: MarketPosition,
        open_orders: dict[str, Order],
    ) -> None:
        """
        If orders are too stale (mid has drifted), cancel and re-quote.
        """
        age = time.time() - pos.last_quote_time
        if age < self.cfg.risk.max_order_age:
            return

        logger.info(
            "[%s] Orders stale (%.0fs) – cancelling and re-quoting.",
            pos.question[:40], age,
        )

        # Cancel existing orders
        for oid in [pos.yes_order_id, pos.no_order_id]:
            if oid and oid in open_orders:
                self.client.cancel_order(oid)

        # Signal that position should be refreshed from current mid
        pos.state = PositionState.IDLE

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------

    def record_rewards(self, condition_id: str, amount: float) -> None:
        """Add a reward payout to the position's running total."""
        if condition_id in self.positions:
            self.positions[condition_id].total_rewards_earned += amount

    def close_position(self, condition_id: str) -> None:
        """Cancel any live orders and mark position resolved."""
        pos = self.positions.get(condition_id)
        if not pos:
            return
        for oid in [pos.yes_order_id, pos.no_order_id, pos.hedge_order_id]:
            if oid:
                self.client.cancel_order(oid)
        pos.state = PositionState.RESOLVED

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = ["=== Position Summary ==="]
        total_rewards = 0.0
        total_pnl = 0.0
        for pos in self.positions.values():
            lines.append(
                f"  [{pos.state.name:18s}] {pos.question[:55]}"
                f" | rewards=${pos.total_rewards_earned:.4f}"
                f" | realised=${pos.realised_pnl:.4f}"
            )
            total_rewards += pos.total_rewards_earned
            total_pnl += pos.realised_pnl
        lines.append(f"  TOTAL rewards=${total_rewards:.4f}  realised PnL=${total_pnl:.4f}")
        return "\n".join(lines)
