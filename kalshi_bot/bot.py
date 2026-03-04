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

logger = logging.getLogger(__name__)


class KalshiBot:
    """
    Orchestrates the Kalshi market-making strategy.

    Instantiate with a BotConfig, then call .run() to start the event loop.
    """

    def __init__(self, config: BotConfig) -> None:
        config.validate()
        self.cfg = config
        self.client = KalshiClient(config)
        self.order_mgr = OrderManager(self.client, config)
        self.budget = BudgetTracker(config.risk.total_budget)
        self._running = False
        self._tick_count = 0

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

        # 3. Sync live balance
        live_balance = self.client.get_balance()
        if live_balance > 0 and abs(live_balance - self.budget.available) > 10:
            logger.info(
                "Balance mismatch: live=$%.2f internal=$%.2f – adjusting.",
                live_balance, self.budget.available,
            )
            self.budget.total = live_balance + sum(
                self.budget._deployed.values()
            )

        # 4. Open new positions
        if self.budget.available >= 5.0:
            self._open_new_positions()

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

            sizing = size_position(market, self.budget.available, self.cfg)

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
