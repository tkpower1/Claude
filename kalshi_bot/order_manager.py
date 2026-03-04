"""
Order manager: places, tracks, and refreshes YES/NO limit orders on Kalshi.

State machine per market position:

    IDLE
      │  (market selected)
      ▼
    QUOTING  ← YES-BUY + NO-BUY orders live
      │   │
      │   │ YES fills
      │   ▼
      │  YES_FILLED  → cancel NO order, place NO-BUY hedge
      │      │
      │      └──────────────────┐
      │                         ▼
      │   NO fills       ONE_SIDE_HEDGED  (wait for hedge fill → resolution)
      ▼   ▼
    BOTH_FILLED  (fully hedged → wait for resolution payout)
      │
      ▼
    RESOLVED → mark IDLE

Kalshi resolves markets by crediting the winning-side contract holders.
At resolution: YES contracts pay $1 if YES wins, $0 if NO wins.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .client import KalshiClient, Order
from .config import BotConfig
from .rewards import compute_scenario_pnl, format_scenario_summary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class PositionState(Enum):
    IDLE = auto()
    QUOTING = auto()
    YES_FILLED = auto()
    NO_FILLED = auto()
    ONE_SIDE_HEDGED = auto()
    BOTH_FILLED = auto()
    RESOLVED = auto()


@dataclass
class MarketPosition:
    """Tracks a single Kalshi market's LP position."""
    ticker: str
    title: str

    # Posted prices (probability 0-1)
    yes_price: float = 0.0
    no_price: float = 0.0

    # Number of contracts per order
    contracts: int = 0

    # Order IDs
    yes_order_id: Optional[str] = None
    no_order_id: Optional[str] = None
    hedge_order_id: Optional[str] = None

    state: PositionState = PositionState.IDLE

    # P&L tracking
    realised_pnl: float = 0.0
    last_quote_time: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Order manager
# ---------------------------------------------------------------------------

class OrderManager:
    """Places and manages YES/NO market-making orders for multiple markets."""

    def __init__(self, client: KalshiClient, config: BotConfig) -> None:
        self.client = client
        self.cfg = config
        self.positions: dict[str, MarketPosition] = {}  # ticker → position

    # ------------------------------------------------------------------
    # Opening a position
    # ------------------------------------------------------------------

    def open_position(
        self,
        ticker: str,
        title: str,
        yes_price: float,
        no_price: float,
        contracts: int,
    ) -> MarketPosition:
        """
        Place YES-BUY and NO-BUY limit orders and record the position.

        yes_price + no_price should be < 1.0 (spread capture profit).
        """
        if ticker in self.positions:
            pos = self.positions[ticker]
            if pos.state not in (PositionState.IDLE, PositionState.RESOLVED):
                logger.warning(
                    "Position %s already open (state=%s) – skipping.",
                    ticker, pos.state.name,
                )
                return pos

        combined = yes_price + no_price
        max_cost = self.cfg.risk.max_fill_cost

        if combined > max_cost:
            logger.warning(
                "%.4f + %.4f = %.4f > %.4f – skipping %s",
                yes_price, no_price, combined, max_cost, ticker,
            )
            pos = MarketPosition(ticker=ticker, title=title, state=PositionState.IDLE)
            self.positions[ticker] = pos
            return pos

        pos = MarketPosition(
            ticker=ticker,
            title=title,
            yes_price=yes_price,
            no_price=no_price,
            contracts=contracts,
        )

        yes_id = self.client.place_limit_order(
            ticker=ticker, side="yes", action="buy",
            price=yes_price, count=contracts,
        )
        no_id = self.client.place_limit_order(
            ticker=ticker, side="no", action="buy",
            price=no_price, count=contracts,
        )

        pos.yes_order_id = yes_id
        pos.no_order_id = no_id
        pos.state = PositionState.QUOTING
        pos.last_quote_time = time.time()

        self.positions[ticker] = pos

        pnl = compute_scenario_pnl(yes_price, no_price, max_cost)
        logger.info(
            "Opened position %s\n%s",
            title[:60],
            format_scenario_summary(pnl, yes_price, no_price),
        )
        return pos

    # ------------------------------------------------------------------
    # Refresh loop
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        """Poll open orders and advance the state machine for all positions."""
        open_orders = {o.order_id: o for o in self.client.get_open_orders()}

        for ticker, pos in list(self.positions.items()):
            try:
                self._refresh_position(pos, open_orders)
            except Exception as exc:
                logger.error("refresh_position(%s): %s", ticker, exc)

    def _refresh_position(
        self,
        pos: MarketPosition,
        open_orders: dict[str, Order],
    ) -> None:
        if pos.state in (PositionState.IDLE, PositionState.RESOLVED):
            return

        yes_live = pos.yes_order_id in open_orders
        no_live = pos.no_order_id in open_orders

        if pos.state == PositionState.QUOTING:
            yes_filled = not yes_live and pos.yes_order_id is not None
            no_filled = not no_live and pos.no_order_id is not None

            if yes_filled and no_filled:
                pnl = 1.0 - (pos.yes_price + pos.no_price)
                pos.realised_pnl += pnl
                pos.state = PositionState.BOTH_FILLED
                logger.info("[%s] BOTH filled – net PnL=%.4f", pos.ticker, pnl)

            elif yes_filled:
                pos.state = PositionState.YES_FILLED
                logger.info("[%s] YES filled @ %.4f – hedging NO…", pos.ticker, pos.yes_price)
                self._hedge_no(pos)

            elif no_filled:
                pos.state = PositionState.NO_FILLED
                logger.info("[%s] NO filled @ %.4f – hedging YES…", pos.ticker, pos.no_price)
                self._hedge_yes(pos)

            else:
                self._maybe_requote(pos, open_orders)

        elif pos.state == PositionState.ONE_SIDE_HEDGED:
            hedge_live = pos.hedge_order_id in open_orders
            if not hedge_live and pos.hedge_order_id is not None:
                pos.state = PositionState.BOTH_FILLED
                logger.info("[%s] Hedge filled – BOTH_FILLED.", pos.ticker)

        elif pos.state == PositionState.BOTH_FILLED:
            pass  # awaiting resolution

    # ------------------------------------------------------------------
    # Hedging
    # ------------------------------------------------------------------

    def _hedge_no(self, pos: MarketPosition) -> None:
        """After YES fills, place a NO-BUY hedge."""
        max_no_price = self.cfg.risk.max_fill_cost - pos.yes_price
        if max_no_price <= 0:
            logger.error("[%s] No headroom for profitable NO hedge.", pos.ticker)
            pos.state = PositionState.ONE_SIDE_HEDGED
            return

        hedge_price = min(max_no_price, 0.99)
        hedge_id = self.client.place_limit_order(
            ticker=pos.ticker, side="no", action="buy",
            price=hedge_price, count=pos.contracts,
        )
        pos.hedge_order_id = hedge_id
        pos.state = PositionState.ONE_SIDE_HEDGED
        logger.info(
            "[%s] NO hedge placed @ %.4f (YES paid %.4f, combined=%.4f)",
            pos.ticker, hedge_price, pos.yes_price, pos.yes_price + hedge_price,
        )

    def _hedge_yes(self, pos: MarketPosition) -> None:
        """After NO fills, place a YES-BUY hedge."""
        max_yes_price = self.cfg.risk.max_fill_cost - pos.no_price
        if max_yes_price <= 0:
            logger.error("[%s] No headroom for profitable YES hedge.", pos.ticker)
            pos.state = PositionState.ONE_SIDE_HEDGED
            return

        hedge_price = min(max_yes_price, 0.99)
        hedge_id = self.client.place_limit_order(
            ticker=pos.ticker, side="yes", action="buy",
            price=hedge_price, count=pos.contracts,
        )
        pos.hedge_order_id = hedge_id
        pos.state = PositionState.ONE_SIDE_HEDGED
        logger.info(
            "[%s] YES hedge placed @ %.4f (NO paid %.4f, combined=%.4f)",
            pos.ticker, hedge_price, pos.no_price, pos.no_price + hedge_price,
        )

    # ------------------------------------------------------------------
    # Re-quoting
    # ------------------------------------------------------------------

    def _maybe_requote(
        self,
        pos: MarketPosition,
        open_orders: dict[str, Order],
    ) -> None:
        age = time.time() - pos.last_quote_time
        if age < self.cfg.risk.max_order_age:
            return

        logger.info(
            "[%s] Orders stale (%.0fs) – cancelling and re-quoting.",
            pos.ticker, age,
        )
        for oid in [pos.yes_order_id, pos.no_order_id]:
            if oid and oid in open_orders:
                self.client.cancel_order(oid)

        pos.state = PositionState.IDLE

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close_position(self, ticker: str) -> None:
        pos = self.positions.get(ticker)
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
        total_pnl = 0.0
        for pos in self.positions.values():
            lines.append(
                f"  [{pos.state.name:18s}] {pos.title[:55]}"
                f" | realised=${pos.realised_pnl:.4f}"
            )
            total_pnl += pos.realised_pnl
        lines.append(f"  TOTAL realised PnL=${total_pnl:.4f}")
        return "\n".join(lines)
