"""
Backtesting engine for the LP / hedge strategy.

Simulation methodology
======================

For each market we replay its minute-by-minute price history.  At each
re-quote interval (default 60 min) the bot places fresh YES and NO orders:

    YES bid = mid_price - depth
    NO  bid = (1 - mid_price) - depth        (depth = v × order_depth_fraction)

Fill detection
--------------
A BUY YES order at price ``p`` fills when the observed price falls to ``p``
or below (i.e. someone sells YES to us cheaper than we bid).

A BUY NO order at price ``p`` fills when the observed YES price rises to
``1 - p`` or above (equivalent: NO price drops to ``p``).

Reward estimation
-----------------
The scoring function S(s) = ((v-s)/v)² × b gives a score for each order.
We estimate our daily reward income as:

    reward = (our_score / total_score_estimate) × daily_pool_USDC

Because we don't have the actual reward pool size, the user must supply
``assumed_daily_reward_pool`` (USDC) per dollar of liquidity.  Typical
Polymarket values are $0.05–$0.50 / day / $1000 deployed.

The three scenarios are tracked and reported:
    1. Neither fills  → collect rewards
    2. One side fills → immediate hedge at worst-case price
    3. Both fill      → fully hedged payout
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from .config import BotConfig, ScoringParams, RiskParams, DEFAULT_CONFIG
from .data_fetcher import MarketHistory, PriceTick
from .rewards import order_score, compute_scenario_pnl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    # How often (minutes) the bot re-quotes its orders
    requote_interval_min: int = 60

    # Fraction of reward window v at which we post orders
    order_depth_fraction: float = 0.40

    # Max spread window v (probability units) — use market default if 0
    default_v: float = 0.05

    # Market reward multiplier b
    multiplier: float = 1.0

    # Assumed daily USDC reward pool for a $1,000 deployed position
    # Typical range on Polymarket: 0.05 – 0.50 USDC/day/$1000
    assumed_daily_pool_per_1k: float = 0.20

    # Total position size (USDC notional)
    position_size: float = 100.0

    # Maximum combined YES+NO cost (fills above this are not hedged profitably)
    max_fill_cost: float = 1.02

    # Taker fee on hedge fill (Polymarket charges ~0.0 for makers, ~0.01 for takers)
    taker_fee: float = 0.01


# ---------------------------------------------------------------------------
# Per-period slot
# ---------------------------------------------------------------------------

@dataclass
class Period:
    """One re-quote window."""
    start_ts:    int
    end_ts:      int
    mid_open:    float    # mid at start of period
    mid_close:   float    # mid at end of period
    yes_bid:     float    # our YES limit price
    no_bid:      float    # our NO limit price
    period_low:  float    # lowest YES price observed this period
    period_high: float    # highest YES price observed this period

    yes_filled:  bool = False
    no_filled:   bool = False
    hedge_price: float = 0.0       # price of hedge (if placed)
    fill_pnl:    float = 0.0       # realised P&L from this fill (at resolution)
    reward_income: float = 0.0


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class MarketResult:
    question:       str
    condition_id:   str
    resolved_yes:   bool
    span_hours:     float
    num_periods:    int

    # P&L breakdown
    total_reward_income:  float = 0.0
    total_fill_pnl:       float = 0.0
    total_fees:           float = 0.0

    # Scenario counters
    periods_neither_filled: int = 0
    periods_one_filled:     int = 0
    periods_both_filled:    int = 0

    # Fill details
    yes_fills: int = 0
    no_fills:  int = 0
    profitable_fills: int = 0
    unprofitable_fills: int = 0

    periods: list[Period] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return self.total_reward_income + self.total_fill_pnl - self.total_fees

    @property
    def roi_pct(self) -> float:
        """Return on invested capital (position_size = 100% basis)."""
        # We use a proxy: reward_income already normalised per $100 position
        return self.net_pnl

    def summary(self) -> str:
        lines = [
            f"Market : {self.question[:70]}",
            f"Outcome: {'YES resolves ✓' if self.resolved_yes else 'NO resolves ✓'}  "
            f"| span={self.span_hours:.1f}h  periods={self.num_periods}",
            f"",
            f"  Scenario 1 – Neither filled : {self.periods_neither_filled:3d} periods",
            f"  Scenario 2 – One side filled: {self.periods_one_filled:3d} periods "
            f"({self.profitable_fills} profitable, {self.unprofitable_fills} unprofitable)",
            f"  Scenario 3 – Both filled    : {self.periods_both_filled:3d} periods",
            f"",
            f"  YES fills : {self.yes_fills}",
            f"  NO  fills : {self.no_fills}",
            f"",
            f"  Reward income : ${self.total_reward_income:7.4f}",
            f"  Fill P&L      : ${self.total_fill_pnl:7.4f}",
            f"  Fees paid     : ${self.total_fees:7.4f}",
            f"  ─────────────────────────",
            f"  Net P&L       : ${self.net_pnl:7.4f}  (on ${100:.0f} position)",
            f"  ROI           : {self.net_pnl:.3f}%  over {self.span_hours:.1f}h",
            f"  Ann. yield    : {self.net_pnl * (8760/max(self.span_hours,1)):.1f}%",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Replays a MarketHistory and simulates the LP/hedge strategy.
    """

    def __init__(self, cfg: BacktestConfig = None) -> None:
        self.cfg = cfg or BacktestConfig()

    def run(self, market: MarketHistory) -> MarketResult:
        """Run the strategy on one market's price history."""
        cfg = self.cfg
        ticks = market.ticks
        if not ticks:
            return MarketResult(
                question=market.question,
                condition_id=market.condition_id,
                resolved_yes=market.resolved_yes,
                span_hours=0,
                num_periods=0,
            )

        result = MarketResult(
            question=market.question,
            condition_id=market.condition_id,
            resolved_yes=market.resolved_yes,
            span_hours=market.span_hours,
            num_periods=0,
        )

        # Slice ticks into re-quote windows
        window_s = cfg.requote_interval_min * 60
        start_ts = ticks[0].timestamp

        idx = 0
        while idx < len(ticks) - 1:
            period_start = ticks[idx].timestamp
            period_end   = period_start + window_s

            # Collect all ticks within this window
            window_ticks: list[PriceTick] = []
            while idx < len(ticks) and ticks[idx].timestamp < period_end:
                window_ticks.append(ticks[idx])
                idx += 1

            if not window_ticks:
                continue

            period = self._simulate_period(
                window_ticks=window_ticks,
                period_end_ts=period_end,
                resolved_yes=market.resolved_yes,
                total_span_s=(ticks[-1].timestamp - ticks[0].timestamp) or 1,
            )
            result.periods.append(period)

        result.num_periods = len(result.periods)
        self._aggregate(result)
        return result

    # ------------------------------------------------------------------
    # Period simulation
    # ------------------------------------------------------------------

    def _simulate_period(
        self,
        window_ticks: list[PriceTick],
        period_end_ts: int,
        resolved_yes: bool,
        total_span_s: int,
    ) -> Period:
        cfg = self.cfg
        v   = cfg.default_v
        depth = v * cfg.order_depth_fraction

        mid_open  = window_ticks[0].price
        mid_close = window_ticks[-1].price
        lo        = min(t.price for t in window_ticks)
        hi        = max(t.price for t in window_ticks)

        # Our order prices
        yes_bid = round(max(mid_open - depth, 0.01), 4)
        no_bid  = round(max((1.0 - mid_open) - depth, 0.01), 4)

        # Combined cost check
        combined = yes_bid + no_bid
        if combined > cfg.max_fill_cost:
            # Squeeze symmetrically
            excess = combined - cfg.max_fill_cost + 0.001
            yes_bid = round(yes_bid - excess / 2, 4)
            no_bid  = round(no_bid  - excess / 2, 4)
            combined = yes_bid + no_bid

        period = Period(
            start_ts=window_ticks[0].timestamp,
            end_ts=period_end_ts,
            mid_open=mid_open,
            mid_close=mid_close,
            yes_bid=yes_bid,
            no_bid=no_bid,
            period_low=lo,
            period_high=hi,
        )

        # Fill detection
        # YES fills: price drops to our YES bid or below
        yes_filled = lo <= yes_bid
        # NO fills: YES price rises to (1 - no_bid) or above
        no_fill_trigger = 1.0 - no_bid
        no_filled = hi >= no_fill_trigger

        # Period duration in hours for reward calculation
        period_hours = (window_ticks[-1].timestamp - window_ticks[0].timestamp) / 3600
        period_hours = max(period_hours, 1 / 60)  # at least 1 minute

        # Reward estimation: score-based, prorated by period duration
        period_reward = self._estimate_period_reward(
            depth=depth, v=v, period_hours=period_hours
        )

        period.reward_income = period_reward

        # P&L from fills
        #
        # Key insight: both YES and NO orders are posted simultaneously at
        # (yes_bid, no_bid) where combined = yes_bid + no_bid ≤ max_fill_cost.
        # When ONE side fills, the OTHER order is STILL live on the book at its
        # posted price — it serves as the natural hedge.  We do NOT need to go
        # to market at a worse price; we simply track the existing NO/YES order.
        #
        # Gross PnL at resolution = 1.00 − (yes_bid + no_bid)
        # This is ALWAYS ≥ 0 when combined ≤ 1.00, and only marginally negative
        # (≤ 2¢) when max_fill_cost = 1.02.  The taker fee applies only to the
        # fill that was triggered (our maker order being hit = 0 fee on most
        # venues; we conservatively apply taker_fee to both sides as the
        # configuration default is 0.01 = 1¢ per $1).
        if yes_filled and no_filled:
            # Scenario 3: both orders filled — fully hedged
            period.yes_filled = True
            period.no_filled  = True
            # Gross: 1.00 − combined; taker fee on both fills
            fill_gross = 1.0 - combined
            period.fill_pnl = (fill_gross - 2 * cfg.taker_fee) * (cfg.position_size / 100)

        elif yes_filled:
            # Scenario 2a: YES filled; existing NO order at no_bid IS the hedge
            period.yes_filled  = True
            period.hedge_price = no_bid          # existing maker order
            hedge_combined     = yes_bid + no_bid   # ≤ max_fill_cost by construction
            fill_gross = 1.0 - hedge_combined
            period.fill_pnl = (fill_gross - cfg.taker_fee) * (cfg.position_size / 100)

        elif no_filled:
            # Scenario 2b: NO filled; existing YES order at yes_bid IS the hedge
            period.no_filled   = True
            period.hedge_price = yes_bid         # existing maker order
            hedge_combined     = yes_bid + no_bid   # ≤ max_fill_cost by construction
            fill_gross = 1.0 - hedge_combined
            period.fill_pnl = (fill_gross - cfg.taker_fee) * (cfg.position_size / 100)
        # else: Scenario 1 – neither fills, reward_income is the income

        return period

    def _estimate_period_reward(
        self, depth: float, v: float, period_hours: float
    ) -> float:
        """
        Estimate USDC reward income for one period based on scoring function.

        We assume our orders earn a share of a daily pool proportional to our
        score S(s) relative to estimated competition.
        """
        cfg = self.cfg
        s   = order_score(depth, v, cfg.multiplier)

        # Assume we're one of ~20 similarly-positioned makers (5% share)
        competition_factor = 20.0
        our_share = s / (s * competition_factor) if s > 0 else 0.0

        # Daily pool scaled to our position size
        daily_pool = cfg.assumed_daily_pool_per_1k * (cfg.position_size / 1000)

        # Prorate by period fraction of a day
        return our_share * daily_pool * (period_hours / 24)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate(self, result: MarketResult) -> None:
        for p in result.periods:
            result.total_reward_income += p.reward_income
            result.total_fees += (
                self.cfg.taker_fee * (self.cfg.position_size / 100)
                if (p.yes_filled or p.no_filled) and p.fill_pnl < 0
                else 0.0
            )

            if p.yes_filled and p.no_filled:
                result.periods_both_filled += 1
                result.yes_fills += 1
                result.no_fills  += 1
            elif p.yes_filled:
                result.periods_one_filled += 1
                result.yes_fills += 1
                result.total_fill_pnl += p.fill_pnl
                if p.fill_pnl >= 0:
                    result.profitable_fills += 1
                else:
                    result.unprofitable_fills += 1
            elif p.no_filled:
                result.periods_one_filled += 1
                result.no_fills += 1
                result.total_fill_pnl += p.fill_pnl
                if p.fill_pnl >= 0:
                    result.profitable_fills += 1
                else:
                    result.unprofitable_fills += 1
            else:
                result.periods_neither_filled += 1

        # Add both-filled P&L
        for p in result.periods:
            if p.yes_filled and p.no_filled:
                result.total_fill_pnl += p.fill_pnl


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------

@dataclass
class PortfolioResult:
    market_results: list[MarketResult]
    backtest_cfg:   BacktestConfig

    @property
    def total_reward_income(self) -> float:
        return sum(r.total_reward_income for r in self.market_results)

    @property
    def total_fill_pnl(self) -> float:
        return sum(r.total_fill_pnl for r in self.market_results)

    @property
    def total_fees(self) -> float:
        return sum(r.total_fees for r in self.market_results)

    @property
    def net_pnl(self) -> float:
        return self.total_reward_income + self.total_fill_pnl - self.total_fees

    def portfolio_summary(self) -> str:
        cfg = self.backtest_cfg
        n   = len(self.market_results)
        total_hours = sum(r.span_hours for r in self.market_results)
        avg_hours   = total_hours / n if n else 0

        s1 = sum(r.periods_neither_filled for r in self.market_results)
        s2 = sum(r.periods_one_filled     for r in self.market_results)
        s3 = sum(r.periods_both_filled    for r in self.market_results)
        total_periods = s1 + s2 + s3

        yes_resolves = sum(1 for r in self.market_results if r.resolved_yes)
        no_resolves  = n - yes_resolves

        lines = [
            "=" * 65,
            "POLYMARKET LP / HEDGE BOT — BACKTEST RESULTS",
            "=" * 65,
            f"",
            f"Markets tested        : {n}",
            f"  YES resolved        : {yes_resolves}",
            f"  NO  resolved        : {no_resolves}",
            f"Total hours covered   : {total_hours:.1f}h  (avg {avg_hours:.1f}h/market)",
            f"Total periods         : {total_periods}",
            f"",
            f"Config",
            f"  Position size       : ${cfg.position_size:.0f} per market",
            f"  Re-quote interval   : {cfg.requote_interval_min} min",
            f"  Order depth (v×frac): {cfg.default_v:.2f} × {cfg.order_depth_fraction:.2f}"
            f" = {cfg.default_v * cfg.order_depth_fraction:.4f}",
            f"  Max fill cost       : ${cfg.max_fill_cost:.2f}",
            f"  Assumed daily pool  : ${cfg.assumed_daily_pool_per_1k:.3f} / $1k",
            f"",
            f"Scenario Distribution",
            f"  Scenario 1 (neither)  : {s1:4d}  ({100*s1/max(total_periods,1):.1f}%)  ← rewards",
            f"  Scenario 2 (one fill) : {s2:4d}  ({100*s2/max(total_periods,1):.1f}%)  ← hedge",
            f"  Scenario 3 (both)     : {s3:4d}  ({100*s3/max(total_periods,1):.1f}%)  ← locked in",
            f"",
            f"P&L Summary (total across all markets)",
            f"  Reward income       : ${self.total_reward_income:9.4f}",
            f"  Fill P&L            : ${self.total_fill_pnl:9.4f}",
            f"  Fees                : ${self.total_fees:9.4f}",
            f"  ─────────────────────────────────────",
            f"  Net P&L             : ${self.net_pnl:9.4f}",
            f"  Per-market avg      : ${self.net_pnl / max(n,1):9.4f}",
            f"",
        ]

        lines.append("Per-Market Breakdown")
        lines.append(f"  {'Market':<45}  {'Hrs':>5}  {'Reward':>7}  {'Fill':>7}  {'Net':>7}  {'Ann%':>6}")
        lines.append("  " + "-" * 82)
        for r in self.market_results:
            ann = r.net_pnl * (8760 / max(r.span_hours, 1))
            lines.append(
                f"  {r.question[:45]:<45}  {r.span_hours:5.1f}"
                f"  ${r.total_reward_income:6.4f}  ${r.total_fill_pnl:6.4f}"
                f"  ${r.net_pnl:6.4f}  {ann:5.1f}%"
            )
        lines.append("=" * 65)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_backtest(
    markets: list[MarketHistory],
    cfg: BacktestConfig = None,
) -> PortfolioResult:
    """
    Run the backtest over a list of MarketHistory objects.

    Parameters
    ----------
    markets : list of MarketHistory objects (from data_fetcher)
    cfg     : BacktestConfig – if None, uses defaults

    Returns
    -------
    PortfolioResult
    """
    cfg = cfg or BacktestConfig()
    engine = BacktestEngine(cfg)
    results = []

    for mh in markets:
        logger.info("Backtesting: %s", mh.question[:60])
        r = engine.run(mh)
        results.append(r)
        logger.info(
            "  → net_pnl=$%.4f  rewards=$%.4f  fills=%d  periods=%d",
            r.net_pnl, r.total_reward_income,
            r.yes_fills + r.no_fills, r.num_periods,
        )

    return PortfolioResult(market_results=results, backtest_cfg=cfg)
