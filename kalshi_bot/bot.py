"""
Main bot orchestrator for Kalshi market-making.

Loop cadence (every `scan_interval` seconds):
  1. Refresh all open positions (detect fills, trigger hedges, re-quote stale orders)
  2. Check USD balance and available budget
  3. Scan for new markets that meet selection criteria
  4. Open new positions on the best markets within budget
  5. Log a periodic state report

Graceful shutdown on SIGINT / SIGTERM.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Optional

from .client import KalshiClient
from .config import BotConfig
from .market_selector import select_markets, title_short
from .order_manager import OrderManager, PositionState
from .position_sizer import BudgetTracker, size_position
from .rewards import compute_scenario_pnl
from .state_store import StateStore
from .vol_estimator import vol_ratio
from .ws_client import KalshiWebSocket

logger = logging.getLogger(__name__)


class KalshiBot:
    """
    Orchestrates the Kalshi market-making strategy.

    Instantiate with a BotConfig, then call .run() to start the event loop.
    """

    def __init__(
        self,
        config: BotConfig,
        state_db: Optional[str] = None,
        data_db: Optional[str] = None,
    ) -> None:
        config.validate()
        self.cfg = config
        self._data_db = data_db   # market_data.db for realized-vol lookups
        self.client = KalshiClient(config)

        store: Optional[StateStore] = None
        if state_db:
            store = StateStore(state_db)

        self.order_mgr = OrderManager(self.client, config, store=store)
        self.budget = BudgetTracker(config.risk.total_budget)
        self._running = False
        self._tick_count = 0

        # WebSocket listener for real-time fill detection.
        # Skipped in dry_run (no real orders to watch).
        self._ws: Optional[KalshiWebSocket] = None
        if not config.dry_run:
            self._ws = KalshiWebSocket(
                config, on_fill=self.order_mgr.handle_ws_fill
            )

        # Restore any live positions that survived a restart
        if store:
            restored = store.load()
            for ticker, pos in restored.items():
                self.order_mgr.positions[ticker] = pos
                # Re-allocate budget for positions still consuming capital
                if pos.contracts > 0 and pos.yes_price > 0:
                    cost = (pos.yes_price + pos.no_price) * pos.contracts
                    self.budget.allocate(ticker, cost)
            if restored:
                logger.info(
                    "Restored %d position(s) from %s. %s",
                    len(restored), state_db, self.budget.summary(),
                )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the bot. Blocks until interrupted."""
        env_label = "DEMO" if self.cfg.demo else "LIVE"
        logger.info(
            "Starting Kalshi Market-Making Bot | env=%s | budget=$%.2f | dry_run=%s",
            env_label, self.cfg.risk.total_budget, self.cfg.dry_run,
        )
        self._install_signal_handlers()
        self._running = True
        if self._ws is not None:
            self._ws.start()

        last_report = 0.0

        while self._running:
            tick_start = time.time()
            self._tick_count += 1

            try:
                self._tick()
            except Exception as exc:
                logger.error(
                    "Unhandled error in tick %d: %s",
                    self._tick_count, exc, exc_info=True,
                )

            if time.time() - last_report >= self.cfg.report_interval:
                self._log_report()
                last_report = time.time()

            elapsed = time.time() - tick_start
            sleep_for = max(0.0, self.cfg.scan_interval - elapsed)
            if sleep_for > 0 and self._running:
                time.sleep(sleep_for)

        if self._ws is not None:
            self._ws.stop()
        logger.info("Bot stopped gracefully.")

    def stop(self) -> None:
        logger.info("Shutdown requested.")
        self._running = False

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        logger.debug("--- Tick %d ---", self._tick_count)

        # 1. Refresh positions
        self.order_mgr.refresh_all()

        # 2. Release budget for resolved/idle positions
        for ticker, pos in list(self.order_mgr.positions.items()):
            if pos.state in (PositionState.RESOLVED, PositionState.IDLE):
                released = self.budget.deployed_in(ticker)
                if released > 0:
                    self.budget.release(ticker)
                    logger.info(
                        "Released $%.2f from closed position %s",
                        released, title_short(pos.title),
                    )

        # 3. Sync live balance using actual portfolio data (cash + positions).
        #    Skip in dry-run: API calls return constants that corrupt the tracker.
        if not self.cfg.dry_run:
            self._sync_portfolio_balance()

        # 4. Open new positions
        if self.budget.available >= 5.0:
            self._open_new_positions()

    # ------------------------------------------------------------------
    # Portfolio balance sync
    # ------------------------------------------------------------------

    def _sync_portfolio_balance(self) -> None:
        """
        Reconcile internal budget tracker against live Kalshi portfolio data.

        cash_balance  = available USD (Kalshi "Cash" column)
        positions_cost = sum of what was paid for currently-held contracts
        true_total    = cash_balance + positions_cost

        This replaces the old heuristic that added _deployed to live_balance,
        which compounded incorrectly on every mismatch.
        """
        cash = self.client.get_balance()
        if cash <= 0:
            return  # API error – don't corrupt the tracker

        positions = self.client.get_portfolio_positions()
        # total_cost is in cents; sum absolute values (long YES or long NO both positive cost)
        positions_cost = sum(
            abs(p.get("total_cost", 0)) / 100.0
            for p in positions
            if p.get("position", 0) != 0
        )
        true_total = cash + positions_cost

        if abs(true_total - self.budget.total) > 1.0:
            logger.info(
                "Portfolio sync: cash=$%.2f positions=$%.2f true_total=$%.2f (internal was $%.2f)",
                cash, positions_cost, true_total, self.budget.total,
            )
            self.budget.total = true_total

        # Also clamp available so it never exceeds actual cash on hand.
        # If internal available > cash, deployed is understated; correct it.
        internal_available = self.budget.available
        if internal_available > cash + 1.0:
            logger.info(
                "Available overstated: internal=$%.2f live_cash=$%.2f – correcting deployed.",
                internal_available, cash,
            )
            self.budget.total = cash + sum(self.budget._deployed.values())

    # ------------------------------------------------------------------
    # Market scan + open
    # ------------------------------------------------------------------

    def _open_new_positions(self) -> None:
        already_active = {
            ticker for ticker, pos in self.order_mgr.positions.items()
            if pos.state not in (PositionState.IDLE, PositionState.RESOLVED)
        }

        try:
            markets = select_markets(
                self.client,
                self.cfg,
                max_markets=self.cfg.risk.order_levels * 5,
            )
        except Exception as exc:
            logger.error("select_markets error: %s", exc)
            return

        opened = 0
        for market in markets:
            if not self._running:
                break
            if market.ticker in already_active:
                continue
            if self.budget.available < 5.0:
                break

            # Vol-spike filter: only enter when short-term realized vol is
            # sufficiently elevated vs the 7-day baseline (min_vol_ratio > 0).
            # This is the core condition for the pre-resolution hypothesis.
            min_ratio = self.cfg.scoring.min_vol_ratio
            if min_ratio > 0 and self._data_db:
                ratio = vol_ratio(market.ticker, self._data_db)
                if ratio is None:
                    logger.debug(
                        "[%s] vol_ratio unavailable (insufficient history) – skipping.",
                        market.ticker,
                    )
                    continue
                if ratio < min_ratio:
                    logger.debug(
                        "[%s] vol_ratio=%.2f < min=%.2f – no spike, skipping.",
                        market.ticker, ratio, min_ratio,
                    )
                    continue
                logger.info(
                    "[%s] Vol spike detected: ratio=%.2fx (threshold=%.2fx) – quoting.",
                    market.ticker, ratio, min_ratio,
                )

            # Fetch order book for order-flow-aware quote adjustment.
            # Best-effort: if it fails, size_position falls back to mid ± depth.
            order_book = None
            if not self.cfg.dry_run:
                try:
                    order_book = self.client.get_order_book(market.ticker)
                except Exception:
                    pass

            sizing = size_position(
                market, self.budget.available, self.cfg,
                data_db=self._data_db,
                order_book=order_book,
            )

            # Guard: spread must be profitable
            pnl = compute_scenario_pnl(
                sizing.yes_price,
                sizing.no_price,
                self.cfg.risk.max_fill_cost,
            )
            if not pnl.one_filled_is_profitable:
                logger.debug(
                    "Skip %s – combined cost %.4f > max %.2f",
                    market.ticker,
                    sizing.yes_price + sizing.no_price,
                    self.cfg.risk.max_fill_cost,
                )
                continue

            if sizing.contracts_per_level < self.cfg.risk.min_order_contracts:
                logger.debug("Skip %s – insufficient budget for min contracts", market.ticker)
                continue

            pos = self.order_mgr.open_position(
                ticker=market.ticker,
                title=market.title,
                yes_price=sizing.yes_price,
                no_price=sizing.no_price,
                contracts=sizing.contracts_per_level,
            )

            if pos.state == PositionState.QUOTING:
                self.budget.allocate(market.ticker, sizing.budget_allocated)
                opened += 1

        if opened:
            logger.info("Opened %d new position(s). %s", opened, self.budget.summary())

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _log_report(self) -> None:
        logger.info("\n%s\n%s", self.order_mgr.summary(), self.budget.summary())

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):
            logger.info("Signal %d received – stopping.", signum)
            self.stop()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def run_bot(config: Optional[BotConfig] = None) -> None:
    from .config import DEFAULT_CONFIG
    cfg = config or DEFAULT_CONFIG
    bot = KalshiBot(cfg)
    bot.run()
