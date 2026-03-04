"""
Backtesting engine for the Kalshi market-making strategy.

Simulates the bot's behaviour against historical (or synthetic) price paths.
For each time step:
  1. Check if any open limit orders are filled (bid/ask crosses our limit).
  2. Advance the position state machine (hedge if one side fills).
  3. Attempt to open new positions if budget allows.
  4. Log all events and P&L.

Fill model:
  YES-BUY order fills when yes_ask (market's offer) drops to or below our price.
  NO-BUY  order fills when no_ask  (= 1 - yes_bid) drops to or below our price.

Resolution model:
  At the end of the price path, YES pays $1 if final mid > 0.50, NO pays $1 otherwise.
  (Simplification; real resolution is binary and determined by the real-world event.)
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .config import BotConfig, MarketFilter
from .fill_model import FillModel, DEFAULT_FILL_MODEL
from .position_sizer import BudgetTracker, size_position
from .rewards import compute_scenario_pnl
from .stats import TTestResult, pnl_ttest_from_results
from .synthetic_data import MarketSnapshot, PricePath, Scenario
from .vol_estimator import realized_vol

logger = logging.getLogger("kalshi_bot.backtester")


# ---------------------------------------------------------------------------
# Simulated order & position
# ---------------------------------------------------------------------------

class PosState(Enum):
    QUOTING = auto()
    YES_FILLED = auto()
    NO_FILLED = auto()
    ONE_SIDE_HEDGED = auto()
    BOTH_FILLED = auto()
    RESOLVED = auto()


@dataclass
class SimOrder:
    order_id: str
    ticker: str
    side: str           # "yes" | "no"
    price: float        # limit price (prob 0-1)
    contracts: int
    filled: bool = False


@dataclass
class SimPosition:
    ticker: str
    yes_price: float
    no_price: float
    contracts: int
    state: PosState = PosState.QUOTING

    yes_order: Optional[SimOrder] = None
    no_order: Optional[SimOrder] = None
    hedge_order: Optional[SimOrder] = None

    open_t: float = 0.0
    open_mid: float = 0.50      # mid-price when position was opened (for drift check)
    close_t: float = 0.0
    realised_pnl: float = 0.0
    resolution_side: str = ""   # "yes" | "no"


# ---------------------------------------------------------------------------
# Per-market backtest result
# ---------------------------------------------------------------------------

@dataclass
class MarketResult:
    ticker: str
    snapshots: int
    positions_opened: int
    positions_both_filled: int
    positions_one_filled: int
    positions_neither_filled: int
    positions_open_at_end: int
    gross_pnl: float             # from filled pairs
    unrealised_pnl: float        # open positions at resolution
    total_pnl: float
    max_drawdown: float
    fill_rate_yes: float         # fraction of YES orders that filled
    fill_rate_no: float
    avg_hold_days: float


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    scenario_name: str
    description: str
    initial_budget: float
    final_budget: float
    net_pnl: float
    return_pct: float
    max_portfolio_drawdown: float
    markets: list[MarketResult]
    events: list[str] = field(default_factory=list)
    # Newey-West HAC t-test on per-position P&L (None if <2 closed positions)
    pnl_ttest: Optional[TTestResult] = None


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class Backtester:
    """
    Runs the market-making strategy against a sequence of MarketSnapshot lists.

    Operates in "event-driven simulation":
      - Snapshots drive time.
      - Orders are filled when market crosses our limit.
      - Position sizing matches the real bot's logic.
    """

    def __init__(
        self,
        config: BotConfig,
        fill_model: FillModel | None = None,
    ) -> None:
        self.cfg = config
        self.fill_model = fill_model or DEFAULT_FILL_MODEL
        self.budget = BudgetTracker(config.risk.total_budget)
        self._order_seq = 0

    def _next_id(self, prefix: str) -> str:
        self._order_seq += 1
        return f"{prefix}-{self._order_seq:04d}"

    # ------------------------------------------------------------------
    # Fill check
    # ------------------------------------------------------------------

    def _yes_order_fills(
        self, order: SimOrder, snap: MarketSnapshot, rng: random.Random
    ) -> bool:
        """
        YES-BUY fills if either:
          (a) the market's YES ask drops to our limit (price crossing), or
          (b) a random market-order arrives and hits our resting bid
              (volume-driven stochastic fill model).

        The stochastic rate is calibrated so that in a $10k-volume/hour
        market a resting order near mid has ~1-2% fill probability per hour.
        """
        # (a) Deterministic: ask crosses our limit
        if snap.yes_ask <= order.price:
            return True
        # (b) Stochastic: market orders hitting our resting bid.
        # P(fill) from MLE-calibrated logistic model (depth, volume, spread).
        depth_from_ask = max(snap.yes_ask - order.price, 0.0)
        hourly_rate = self.fill_model.predict(
            depth=depth_from_ask,
            volume_usd=snap.volume_usd,
            spread=snap.spread,
        )
        return rng.random() < hourly_rate

    def _no_order_fills(
        self, order: SimOrder, snap: MarketSnapshot, rng: random.Random
    ) -> bool:
        """NO-BUY fills when (1 - yes_bid) ≤ our limit, or stochastic."""
        no_ask = 1.0 - snap.yes_bid
        if no_ask <= order.price:
            return True
        depth_from_ask = max(no_ask - order.price, 0.0)
        hourly_rate = self.fill_model.predict(
            depth=depth_from_ask,
            volume_usd=snap.volume_usd,
            spread=snap.spread,
        )
        return rng.random() < hourly_rate

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(snap: MarketSnapshot) -> str:
        """Simple resolution: YES wins if final mid > 0.50, else NO wins."""
        return "yes" if snap.mid > 0.50 else "no"

    def _calc_resolution_pnl(self, pos: SimPosition, resolution: str) -> float:
        """
        Compute P&L at resolution given the position state and which side won.

        Rules:
          YES contract: pays $1 if YES wins, $0 if NO wins.
          NO  contract: pays $1 if NO  wins, $0 if YES wins.
          Cost = price * contracts for each side held.
        """
        pnl = 0.0
        contracts = pos.contracts

        fee_rate = self.cfg.risk.fee_rate

        if pos.state == PosState.BOTH_FILLED:
            # Held both YES and NO → one pays $1, other pays $0
            # Profit = 1.0 - (yes_price + no_price) per contract, minus fees on both fills
            fee = fee_rate * (pos.yes_price + pos.no_price) * contracts
            pnl = (1.0 - pos.yes_price - pos.no_price) * contracts - fee

        elif pos.state == PosState.ONE_SIDE_HEDGED and pos.hedge_order:
            # One original side filled + hedge order placed (may or may not have filled).
            hedge_filled = pos.hedge_order.filled
            if pos.yes_order and pos.yes_order.filled:
                yes_cost = pos.yes_price
                if hedge_filled:
                    fee = fee_rate * (yes_cost + pos.hedge_order.price) * contracts
                    pnl = (1.0 - yes_cost - pos.hedge_order.price) * contracts - fee
                else:
                    fee = fee_rate * yes_cost * contracts
                    pnl = ((1.0 if resolution == "yes" else 0.0) - yes_cost) * contracts - fee
            elif pos.no_order and pos.no_order.filled:
                no_cost = pos.no_price
                if hedge_filled:
                    fee = fee_rate * (no_cost + pos.hedge_order.price) * contracts
                    pnl = (1.0 - no_cost - pos.hedge_order.price) * contracts - fee
                else:
                    fee = fee_rate * no_cost * contracts
                    pnl = ((1.0 if resolution == "no" else 0.0) - no_cost) * contracts - fee

        elif pos.state in (PosState.QUOTING, PosState.YES_FILLED, PosState.NO_FILLED):
            # Still open (unhedged) at resolution
            if pos.yes_order and pos.yes_order.filled:
                payout = 1.0 if resolution == "yes" else 0.0
                pnl = (payout - pos.yes_price) * contracts
            if pos.no_order and pos.no_order.filled:
                payout = 1.0 if resolution == "no" else 0.0
                pnl += (payout - pos.no_price) * contracts

        return round(pnl, 4)

    # ------------------------------------------------------------------
    # Market filter (simplified for backtester)
    # ------------------------------------------------------------------

    def _passes_filter(self, snap: MarketSnapshot) -> bool:
        f = self.cfg.market_filter
        mid = snap.mid
        return (
            f.min_mid <= mid <= f.max_mid
            and snap.spread >= f.min_spread
            and snap.open_interest <= f.max_open_interest
        )

    # ------------------------------------------------------------------
    # Core: simulate one market
    # ------------------------------------------------------------------

    def run_market(
        self,
        snapshots: list[MarketSnapshot],
        seed_offset: int = 0,
    ) -> MarketResult:
        ticker = snapshots[0].ticker if snapshots else "UNKNOWN"
        positions: list[SimPosition] = []
        active_pos: Optional[SimPosition] = None  # one position at a time per market
        rng = random.Random(seed_offset * 7919 + 12345)

        peak_budget = self.budget.available
        max_dd = 0.0
        gross_pnl = 0.0
        unrealised_pnl = 0.0
        yes_orders_placed = 0
        yes_orders_filled = 0
        no_orders_placed = 0
        no_orders_filled = 0
        hold_days: list[float] = []

        # Rolling window of mid-prices for realized-vol estimation.
        # 24 snapshots = 24 hours of hourly data (matches PricePath dt=1/24 default).
        _vol_window_size = 24
        _mid_window: list[float] = []

        for snap in snapshots:
            # ── Check existing position ──────────────────────────────────
            if active_pos is not None and active_pos.state not in (
                PosState.RESOLVED,
            ):
                pos = active_pos

                if pos.state == PosState.QUOTING:
                    # ── Pre-fill drift cancel ─────────────────────────────
                    # If mid has moved too far from where we placed the order,
                    # cancel before any fill can register (mirrors real-time
                    # order cancellation on price shock in production).
                    mid_drift = abs(snap.mid - pos.open_mid)
                    yes_f, no_f = False, False
                    if mid_drift > self.cfg.risk.cancel_if_mid_drift:
                        self.budget.release(ticker)
                        hold_days.append(snap.t - pos.open_t)
                        pos.state = PosState.RESOLVED
                        active_pos = None
                        logger.debug(
                            "[%s t=%.2f] drift cancel: |%.3f-%.3f|=%.3f > %.3f",
                            ticker, snap.t, snap.mid, pos.open_mid,
                            mid_drift, self.cfg.risk.cancel_if_mid_drift,
                        )
                    else:
                        yes_f = pos.yes_order and self._yes_order_fills(pos.yes_order, snap, rng)
                        no_f = pos.no_order and self._no_order_fills(pos.no_order, snap, rng)

                    if yes_f:
                        pos.yes_order.filled = True
                        yes_orders_filled += 1
                    if no_f:
                        pos.no_order.filled = True
                        no_orders_filled += 1

                    if yes_f and no_f:
                        pos.state = PosState.BOTH_FILLED
                        logger.debug("[%s t=%.2f] BOTH filled", ticker, snap.t)
                    elif yes_f:
                        pos.state = PosState.YES_FILLED
                        # Hedge: place NO-BUY at max profitable price
                        max_no = self.cfg.risk.max_fill_cost - pos.yes_price
                        hedge_price = min(max_no, snap.yes_ask)  # use current ask
                        if hedge_price > 0:
                            h = SimOrder(
                                order_id=self._next_id("H"),
                                ticker=ticker,
                                side="no",
                                price=round(min(hedge_price, 0.99), 4),
                                contracts=pos.contracts,
                            )
                            pos.hedge_order = h
                            pos.state = PosState.ONE_SIDE_HEDGED
                    elif no_f:
                        pos.state = PosState.NO_FILLED
                        max_yes = self.cfg.risk.max_fill_cost - pos.no_price
                        hedge_price = min(max_yes, 1.0 - snap.yes_bid)
                        if hedge_price > 0:
                            h = SimOrder(
                                order_id=self._next_id("H"),
                                ticker=ticker,
                                side="yes",
                                price=round(min(hedge_price, 0.99), 4),
                                contracts=pos.contracts,
                            )
                            pos.hedge_order = h
                            pos.state = PosState.ONE_SIDE_HEDGED

                elif pos.state == PosState.ONE_SIDE_HEDGED and pos.hedge_order:
                    h = pos.hedge_order
                    if h.side == "yes" and self._yes_order_fills(h, snap, rng):
                        h.filled = True
                        pos.state = PosState.BOTH_FILLED
                        yes_orders_filled += 1
                    elif h.side == "no" and self._no_order_fills(h, snap, rng):
                        h.filled = True
                        pos.state = PosState.BOTH_FILLED
                        no_orders_filled += 1
                    # ── Directional stop-loss if hedge is still open ──────
                    if not h.filled:
                        # gap = how far the current market ask is above our hedge limit
                        # positive → market has moved away from us
                        if h.side == "no":
                            gap = (1.0 - snap.yes_bid) - h.price   # no_ask - hedge_limit
                        else:
                            gap = snap.yes_ask - h.price            # yes_ask - hedge_limit
                        if gap > self.cfg.risk.hedge_stop_gap:
                            # Cut the losing leg at current mark-to-market (fee on filled side)
                            if pos.yes_order and pos.yes_order.filled:
                                fee = self.cfg.risk.fee_rate * pos.yes_price * pos.contracts
                                mtm_pnl = (snap.yes_bid - pos.yes_price) * pos.contracts - fee
                            else:
                                fee = self.cfg.risk.fee_rate * pos.no_price * pos.contracts
                                mtm_pnl = ((1.0 - snap.yes_ask) - pos.no_price) * pos.contracts - fee
                            gross_pnl += mtm_pnl
                            pos.realised_pnl = mtm_pnl
                            hold_days.append(snap.t - pos.open_t)
                            self.budget.release(ticker)
                            pos.close_t = snap.t
                            pos.state = PosState.RESOLVED
                            active_pos = None
                            logger.debug(
                                "[%s t=%.2f] hedge stop: gap=%.3f > %.3f mtm_pnl=%.2f",
                                ticker, snap.t, gap, self.cfg.risk.hedge_stop_gap, mtm_pnl,
                            )

                # ── Cycle: BOTH_FILLED → immediately resolve and redeploy ─
                if pos.state == PosState.BOTH_FILLED:
                    fee = self.cfg.risk.fee_rate * (pos.yes_price + pos.no_price) * pos.contracts
                    locked_pnl = (1.0 - pos.yes_price - pos.no_price) * pos.contracts - fee
                    gross_pnl += locked_pnl
                    pos.realised_pnl = locked_pnl
                    hold_days.append(snap.t - pos.open_t)
                    self.budget.release(ticker)
                    pos.close_t = snap.t
                    pos.state = PosState.RESOLVED
                    active_pos = None

                # ── Stale-quote requote ───────────────────────────────────
                elif pos.state == PosState.QUOTING:
                    age_hours = (snap.t - pos.open_t) * 24
                    if age_hours > self.cfg.risk.max_order_age / 3600:
                        # Cancel, release budget, allow reopen at current prices
                        self.budget.release(ticker)
                        hold_days.append(snap.t - pos.open_t)
                        pos.state = PosState.RESOLVED
                        active_pos = None

            # ── Maintain rolling mid-price window for vol estimation ─────
            _mid_window.append(snap.mid)
            if len(_mid_window) > _vol_window_size:
                _mid_window.pop(0)

            # ── Open a new position if none active ───────────────────────
            if (active_pos is None or active_pos.state == PosState.RESOLVED) \
                    and self.budget.available >= 5.0 \
                    and self._passes_filter(snap):

                # Rolling realized vol: dt = 1h (PricePath default step size)
                rv = realized_vol(_mid_window, dt_hours=1.0)
                vol_for_sizing = max(rv, self.cfg.scoring.default_v) if rv else None

                # Simple sizing from the real bot's logic
                from .client import MarketInfo as _MI
                fake_info = _MI(
                    ticker=ticker,
                    title=ticker,
                    yes_bid=snap.yes_bid,
                    yes_ask=snap.yes_ask,
                    no_bid=1 - snap.yes_ask,
                    no_ask=1 - snap.yes_bid,
                    mid_price=snap.mid,
                    spread=snap.spread,
                    volume_24h=snap.volume_usd,
                    open_interest=snap.open_interest,
                    close_time="",
                    status="open",
                )
                sizing = size_position(
                    fake_info, self.budget.available, self.cfg,
                    vol_override=vol_for_sizing,
                )

                pnl_check = compute_scenario_pnl(
                    sizing.yes_price, sizing.no_price,
                    self.cfg.risk.max_fill_cost,
                )
                if not pnl_check.one_filled_is_profitable:
                    continue
                if sizing.contracts_per_level < 1:
                    continue

                yes_o = SimOrder(self._next_id("Y"), ticker, "yes",
                                 sizing.yes_price, sizing.contracts_per_level)
                no_o = SimOrder(self._next_id("N"), ticker, "no",
                                sizing.no_price, sizing.contracts_per_level)
                yes_orders_placed += 1
                no_orders_placed += 1

                pos = SimPosition(
                    ticker=ticker,
                    yes_price=sizing.yes_price,
                    no_price=sizing.no_price,
                    contracts=sizing.contracts_per_level,
                    yes_order=yes_o,
                    no_order=no_o,
                    open_t=snap.t,
                    open_mid=snap.mid,
                )
                self.budget.allocate(ticker, sizing.budget_allocated)
                active_pos = pos
                positions.append(pos)

            # ── Track drawdown ───────────────────────────────────────────
            avail = self.budget.available
            if avail > peak_budget:
                peak_budget = avail
            dd = (peak_budget - avail) / max(peak_budget, 1e-9)
            if dd > max_dd:
                max_dd = dd

        # ── Resolve all open positions at end of path ────────────────────
        final_snap = snapshots[-1] if snapshots else None
        if final_snap:
            resolution = self._resolve(final_snap)
            for pos in positions:
                if pos.state != PosState.RESOLVED:
                    pnl = self._calc_resolution_pnl(pos, resolution)
                    hold = final_snap.t - pos.open_t
                    if pos.state == PosState.BOTH_FILLED:
                        gross_pnl += pos.realised_pnl or pnl
                    else:
                        unrealised_pnl += pnl
                    hold_days.append(hold)
                    self.budget.release(pos.ticker)
                    pos.close_t = final_snap.t
                    pos.realised_pnl = pnl
                    pos.state = PosState.RESOLVED

        # Categorise
        both_filled = sum(1 for p in positions if p.state == PosState.BOTH_FILLED
                          or (p.realised_pnl > 0 and p.yes_order and p.yes_order.filled
                              and p.no_order and p.no_order.filled))
        one_filled = sum(
            1 for p in positions
            if (p.yes_order and p.yes_order.filled) != (p.no_order and p.no_order.filled)
        )
        neither = sum(
            1 for p in positions
            if not (p.yes_order and p.yes_order.filled) and not (p.no_order and p.no_order.filled)
        )
        open_at_end = sum(1 for p in positions if p.state != PosState.RESOLVED)

        return MarketResult(
            ticker=ticker,
            snapshots=len(snapshots),
            positions_opened=len(positions),
            positions_both_filled=both_filled,
            positions_one_filled=one_filled,
            positions_neither_filled=neither,
            positions_open_at_end=open_at_end,
            gross_pnl=round(gross_pnl, 4),
            unrealised_pnl=round(unrealised_pnl, 4),
            total_pnl=round(gross_pnl + unrealised_pnl, 4),
            max_drawdown=round(max_dd, 4),
            fill_rate_yes=yes_orders_filled / max(yes_orders_placed, 1),
            fill_rate_no=no_orders_filled / max(no_orders_placed, 1),
            avg_hold_days=sum(hold_days) / max(len(hold_days), 1),
        )

    # ------------------------------------------------------------------
    # Run a full scenario
    # ------------------------------------------------------------------

    def run_scenario(self, scenario: Scenario, seed: int = 42) -> ScenarioResult:
        """Simulate all markets in a scenario and aggregate results."""
        # Reset budget for each scenario
        self.budget = BudgetTracker(self.cfg.risk.total_budget)

        market_results: list[MarketResult] = []
        events: list[str] = []

        for i, mkt_kwargs in enumerate(scenario.markets):
            ticker = mkt_kwargs.get("ticker", f"MKT-{i}")
            path = PricePath(seed=seed + i * 1000, **mkt_kwargs)
            snapshots = path.generate(scenario.days)

            # Black-swan scenario: inject a late price shock
            if scenario.name == "black_swan" and snapshots:
                shock_idx = int(len(snapshots) * 0.75)
                shock_mid = 0.02 if i == 0 else 0.98
                for snap in snapshots[shock_idx:]:
                    snap.yes_bid = round(max(shock_mid - 0.01, 0.01), 2)
                    snap.yes_ask = round(min(shock_mid + 0.01, 0.99), 2)
                    snap.mid = shock_mid
                    snap.spread = 0.02
                events.append(f"{ticker}: black-swan shock at t≈{snapshots[shock_idx].t:.1f}d")

            result = self.run_market(snapshots, seed_offset=i)
            market_results.append(result)
            events.append(
                f"{ticker}: opened={result.positions_opened} "
                f"both_fill={result.positions_both_filled} "
                f"pnl=${result.total_pnl:+.2f}"
            )

        total_pnl = sum(r.total_pnl for r in market_results)
        max_dd = max((r.max_drawdown for r in market_results), default=0.0)
        final_budget = self.cfg.risk.total_budget + total_pnl
        ret_pct = total_pnl / max(self.cfg.risk.total_budget, 1) * 100

        # Newey-West HAC t-test: is mean P&L significantly different from zero?
        ttest = pnl_ttest_from_results(market_results)

        return ScenarioResult(
            scenario_name=scenario.name,
            description=scenario.description,
            initial_budget=self.cfg.risk.total_budget,
            final_budget=round(final_budget, 2),
            net_pnl=round(total_pnl, 4),
            return_pct=round(ret_pct, 2),
            max_portfolio_drawdown=round(max_dd, 4),
            markets=market_results,
            events=events,
            pnl_ttest=ttest,
        )
