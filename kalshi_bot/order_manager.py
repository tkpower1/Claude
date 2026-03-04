"""
Order manager: places, tracks, and refreshes YES/NO limit orders on Kalshi.

State machine per market position:

    IDLE
      │  (market selected)
      ▼
    QUOTING  ← YES-BUY + NO-BUY orders live
      │   │   │
      │   │   │ mid drifts > cancel_if_mid_drift → cancel both → IDLE
      │   │   │
      │   │ YES fills
      │   ▼
      │  YES_FILLED  → cancel NO order, place NO-BUY hedge → ONE_SIDE_HEDGED
      │                   │
      │   NO fills        │ hedge_stop_gap exceeded → cancel hedge, MTM exit → RESOLVED
      ▼   ▼               │
    BOTH_FILLED ←─────────┘ (hedge fills)
      │
      │ market status = settled/finalized/closed
      ▼
    RESOLVED → budget released

P&L accounting (all paths subtract Kalshi fee_rate per fill):
  BOTH_FILLED   : (1 - yes_price - no_price) × contracts - fee_YES - fee_NO
  Stop-loss exit: (current_bid - leg_price)  × contracts - fee_leg
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

from .client import KalshiClient, Order, OrderBook
from .config import BotConfig
from .rewards import compute_scenario_pnl, format_scenario_summary

if TYPE_CHECKING:
    from .state_store import StateStore

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


# Market statuses Kalshi returns when a market has finished paying out
_RESOLVED_STATUSES = frozenset({"settled", "finalized", "closed", "resolved"})


@dataclass
class MarketPosition:
    """Tracks a single Kalshi market's LP position."""
    ticker: str
    title: str

    # Posted prices (probability 0-1)
    yes_price: float = 0.0
    no_price: float = 0.0

    # Market mid when we first quoted (used for drift detection)
    original_mid: float = 0.0

    # Number of contracts per order
    contracts: int = 0

    # Order IDs
    yes_order_id: Optional[str] = None
    no_order_id: Optional[str] = None
    hedge_order_id: Optional[str] = None

    # Hedge leg metadata (set when hedge is placed)
    hedge_price: float = 0.0     # price we paid for the hedge
    filled_side: str = ""        # "yes" or "no" — which original side filled first

    state: PositionState = PositionState.IDLE

    # P&L tracking (net of fees, in USD)
    realised_pnl: float = 0.0
    last_quote_time: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Order manager
# ---------------------------------------------------------------------------

class OrderManager:
    """Places and manages YES/NO market-making orders for multiple markets."""

    def __init__(
        self,
        client: KalshiClient,
        config: BotConfig,
        store: Optional["StateStore"] = None,
    ) -> None:
        self.client = client
        self.cfg = config
        self._store = store
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

        yes_price + no_price must be < max_fill_cost (spread profit).
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

        # mid ≈ mean of our YES ask and NO ask (both from the taker's perspective)
        original_mid = (yes_price + (1.0 - no_price)) / 2.0

        pos = MarketPosition(
            ticker=ticker,
            title=title,
            yes_price=yes_price,
            no_price=no_price,
            original_mid=original_mid,
            contracts=contracts,
        )

        yes_id = self.client.place_limit_order(
            ticker=ticker, side="yes", action="buy",
            price=yes_price, count=contracts,
        )
        if yes_id is None:
            logger.error("[%s] YES order placement failed – aborting.", ticker)
            pos.state = PositionState.IDLE
            self.positions[ticker] = pos
            return pos

        no_id = self.client.place_limit_order(
            ticker=ticker, side="no", action="buy",
            price=no_price, count=contracts,
        )
        if no_id is None:
            logger.error("[%s] NO order placement failed – cancelling YES.", ticker)
            self.client.cancel_order(yes_id)
            pos.state = PositionState.IDLE
            self.positions[ticker] = pos
            return pos

        pos.yes_order_id = yes_id
        pos.no_order_id = no_id
        pos.state = PositionState.QUOTING
        pos.last_quote_time = time.time()

        self.positions[ticker] = pos
        self._save(pos)

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
            # Fetch live market data for positions that need price / status checks
            order_book: Optional[OrderBook] = None
            market_status: Optional[str] = None

            if not self.cfg.dry_run:
                if pos.state in (PositionState.QUOTING, PositionState.ONE_SIDE_HEDGED):
                    try:
                        order_book = self.client.get_order_book(ticker)
                    except Exception as exc:
                        logger.debug("get_order_book(%s): %s", ticker, exc)

                if pos.state == PositionState.BOTH_FILLED:
                    try:
                        raw = self.client.get_market(ticker)
                        market_status = raw.get("status", "open").lower()
                    except Exception as exc:
                        logger.debug("get_market(%s): %s", ticker, exc)

            try:
                self._refresh_position(pos, open_orders, order_book, market_status)
            except Exception as exc:
                logger.error("refresh_position(%s): %s", ticker, exc, exc_info=True)

    def _refresh_position(
        self,
        pos: MarketPosition,
        open_orders: dict[str, Order],
        order_book: Optional[OrderBook] = None,
        market_status: Optional[str] = None,
    ) -> None:
        if pos.state in (PositionState.IDLE, PositionState.RESOLVED):
            return

        yes_live = pos.yes_order_id in open_orders
        no_live = pos.no_order_id in open_orders

        # ── QUOTING: waiting for either side to fill ───────────────────
        if pos.state == PositionState.QUOTING:
            yes_filled = not yes_live and pos.yes_order_id is not None
            no_filled = not no_live and pos.no_order_id is not None

            if yes_filled and no_filled:
                self._record_both_filled(pos)

            elif yes_filled:
                pos.state = PositionState.YES_FILLED
                pos.filled_side = "yes"
                logger.info("[%s] YES filled @ %.4f – hedging NO…", pos.ticker, pos.yes_price)
                self._hedge_no(pos)

            elif no_filled:
                pos.state = PositionState.NO_FILLED
                pos.filled_side = "no"
                logger.info("[%s] NO filled @ %.4f – hedging YES…", pos.ticker, pos.no_price)
                self._hedge_yes(pos)

            else:
                # Neither filled yet: check mid drift
                if order_book is not None:
                    drift = abs(order_book.mid - pos.original_mid)
                    if drift > self.cfg.risk.cancel_if_mid_drift:
                        logger.info(
                            "[%s] Mid drifted %.4f (limit %.4f) – cancelling orders.",
                            pos.ticker, drift, self.cfg.risk.cancel_if_mid_drift,
                        )
                        for oid in [pos.yes_order_id, pos.no_order_id]:
                            if oid and oid in open_orders:
                                self.client.cancel_order(oid)
                        pos.state = PositionState.IDLE
                        self._save(pos)
                        return

                self._maybe_requote(pos, open_orders)

        # ── ONE_SIDE_HEDGED: waiting for hedge to fill ─────────────────
        elif pos.state == PositionState.ONE_SIDE_HEDGED:
            hedge_live = pos.hedge_order_id in open_orders

            if not hedge_live and pos.hedge_order_id is not None:
                # Hedge filled – both sides now held
                self._record_both_filled_after_hedge(pos)

            elif order_book is not None and pos.hedge_order_id in open_orders:
                # Hedge still resting – check stop-loss
                self._check_hedge_stop(pos, order_book, open_orders)

        # ── BOTH_FILLED: waiting for market to resolve ─────────────────
        elif pos.state == PositionState.BOTH_FILLED:
            if market_status in _RESOLVED_STATUSES:
                pos.state = PositionState.RESOLVED
                self._save(pos)
                logger.info(
                    "[%s] Market resolved (%s) – releasing. total_pnl=$%.4f",
                    pos.ticker, market_status, pos.realised_pnl,
                )

    # ------------------------------------------------------------------
    # P&L helpers
    # ------------------------------------------------------------------

    def _fee(self, price: float, contracts: int) -> float:
        return self.cfg.risk.fee_rate * price * contracts

    def _record_both_filled(self, pos: MarketPosition) -> None:
        """Both YES and NO filled simultaneously from the QUOTING state."""
        fee = self._fee(pos.yes_price, pos.contracts) + self._fee(pos.no_price, pos.contracts)
        pnl = (1.0 - pos.yes_price - pos.no_price) * pos.contracts - fee
        pos.realised_pnl += pnl
        pos.state = PositionState.BOTH_FILLED
        self._save(pos)
        logger.info(
            "[%s] BOTH filled – spread=$%.4f fee=$%.4f net=$%.4f",
            pos.ticker,
            (1.0 - pos.yes_price - pos.no_price) * pos.contracts,
            fee, pnl,
        )

    def _record_both_filled_after_hedge(self, pos: MarketPosition) -> None:
        """Hedge filled – now holding both sides."""
        if pos.filled_side == "yes":
            leg1_price, leg2_price = pos.yes_price, pos.hedge_price
        else:
            leg1_price, leg2_price = pos.no_price, pos.hedge_price
        fee = self._fee(leg1_price, pos.contracts) + self._fee(leg2_price, pos.contracts)
        pnl = (1.0 - leg1_price - leg2_price) * pos.contracts - fee
        pos.realised_pnl += pnl
        pos.state = PositionState.BOTH_FILLED
        self._save(pos)
        logger.info(
            "[%s] Hedge filled – spread=$%.4f fee=$%.4f net=$%.4f",
            pos.ticker,
            (1.0 - leg1_price - leg2_price) * pos.contracts,
            fee, pnl,
        )

    # ------------------------------------------------------------------
    # Hedging
    # ------------------------------------------------------------------

    def _hedge_no(self, pos: MarketPosition) -> None:
        """After YES fills, place a NO-BUY hedge."""
        max_no_price = self.cfg.risk.max_fill_cost - pos.yes_price
        if max_no_price <= 0:
            logger.error("[%s] No headroom for profitable NO hedge.", pos.ticker)
            pos.state = PositionState.ONE_SIDE_HEDGED
            self._save(pos)
            return

        hedge_price = min(max_no_price, 0.99)
        hedge_id = self.client.place_limit_order(
            ticker=pos.ticker, side="no", action="buy",
            price=hedge_price, count=pos.contracts,
        )
        pos.hedge_order_id = hedge_id
        pos.hedge_price = hedge_price
        pos.state = PositionState.ONE_SIDE_HEDGED
        self._save(pos)
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
            self._save(pos)
            return

        hedge_price = min(max_yes_price, 0.99)
        hedge_id = self.client.place_limit_order(
            ticker=pos.ticker, side="yes", action="buy",
            price=hedge_price, count=pos.contracts,
        )
        pos.hedge_order_id = hedge_id
        pos.hedge_price = hedge_price
        pos.state = PositionState.ONE_SIDE_HEDGED
        self._save(pos)
        logger.info(
            "[%s] YES hedge placed @ %.4f (NO paid %.4f, combined=%.4f)",
            pos.ticker, hedge_price, pos.no_price, pos.no_price + hedge_price,
        )

    # ------------------------------------------------------------------
    # Stop-loss
    # ------------------------------------------------------------------

    def _check_hedge_stop(
        self,
        pos: MarketPosition,
        order_book: OrderBook,
        open_orders: dict[str, Order],
    ) -> None:
        """
        If the unfilled leg has moved adversely beyond hedge_stop_gap,
        cancel the hedge and cut the filled leg at the current market bid.
        """
        gap = self.cfg.risk.hedge_stop_gap

        if pos.filled_side == "yes":
            # YES filled; hedge is NO. Adverse move = yes_ask rising above yes_price.
            current_yes_ask = order_book.yes_asks[0][0] if order_book.yes_asks else pos.yes_price
            adverse_drift = current_yes_ask - pos.yes_price
            if adverse_drift > gap:
                current_yes_bid = order_book.yes_bids[0][0] if order_book.yes_bids else 0.0
                fee = self._fee(pos.yes_price, pos.contracts)
                mtm = (current_yes_bid - pos.yes_price) * pos.contracts - fee
                self._cut_position(pos, mtm, open_orders, "YES leg stop-loss")

        elif pos.filled_side == "no":
            # NO filled; hedge is YES. Adverse move = no_ask rising (yes_bid falling).
            current_no_ask = (1.0 - order_book.yes_bids[0][0]) if order_book.yes_bids else pos.no_price
            adverse_drift = current_no_ask - pos.no_price
            if adverse_drift > gap:
                current_no_bid = (1.0 - order_book.yes_asks[0][0]) if order_book.yes_asks else 0.0
                fee = self._fee(pos.no_price, pos.contracts)
                mtm = (current_no_bid - pos.no_price) * pos.contracts - fee
                self._cut_position(pos, mtm, open_orders, "NO leg stop-loss")

    def _cut_position(
        self,
        pos: MarketPosition,
        mtm_pnl: float,
        open_orders: dict[str, Order],
        reason: str,
    ) -> None:
        """Cancel the hedge and record a MTM stop-loss exit."""
        if pos.hedge_order_id and pos.hedge_order_id in open_orders:
            self.client.cancel_order(pos.hedge_order_id)
        pos.realised_pnl += mtm_pnl
        pos.state = PositionState.RESOLVED
        self._save(pos)
        logger.warning(
            "[%s] %s – mtm=$%.4f total_pnl=$%.4f",
            pos.ticker, reason, mtm_pnl, pos.realised_pnl,
        )

    # ------------------------------------------------------------------
    # Re-quoting (stale order cancellation)
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
        self._save(pos)

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
        self._save(pos)

    # ------------------------------------------------------------------
    # State store helper
    # ------------------------------------------------------------------

    def _save(self, pos: MarketPosition) -> None:
        if self._store is not None:
            self._store.upsert(pos)

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
