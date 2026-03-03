"""
Unit tests for the Polymarket backtest engine.

All tests use synthetic price histories — no live API calls.
"""

from __future__ import annotations

import pytest
from polymarket_bot.backtest import BacktestConfig, BacktestEngine, run_backtest
from polymarket_bot.data_fetcher import MarketHistory, PriceTick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(
    prices: list[float],
    interval_s: int = 600,      # 10-min ticks
    resolved_yes: bool = True,
    question: str = "Test market?",
) -> MarketHistory:
    """Build a synthetic MarketHistory from a list of prices."""
    start_ts = 1_700_000_000
    ticks = [
        PriceTick(timestamp=start_ts + i * interval_s, price=p)
        for i, p in enumerate(prices)
    ]
    return MarketHistory(
        condition_id="test-cond",
        question=question,
        yes_token_id="yes-tok",
        no_token_id="no-tok",
        start_date="2024-01-01",
        end_date="2024-01-02",
        ticks=ticks,
        resolved_yes=resolved_yes,
    )


def _default_cfg(**kwargs) -> BacktestConfig:
    # Build defaults then let kwargs override without duplicate-key errors
    defaults = dict(
        requote_interval_min=60,
        order_depth_fraction=0.40,
        default_v=0.05,
        assumed_daily_pool_per_1k=0.20,
        position_size=100.0,
        max_fill_cost=1.02,
        taker_fee=0.01,
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_defaults_are_sensible(self):
        cfg = BacktestConfig()
        assert 0 < cfg.order_depth_fraction < 1
        assert cfg.max_fill_cost <= 1.05
        assert cfg.position_size > 0
        assert cfg.taker_fee >= 0

    def test_order_depth_never_exceeds_v(self):
        cfg = _default_cfg()
        depth = cfg.default_v * cfg.order_depth_fraction
        assert depth < cfg.default_v


# ---------------------------------------------------------------------------
# Scenario 1: price never moves – no fills, only rewards
# ---------------------------------------------------------------------------

class TestScenario1NeitherFills:
    """Price sits at mid throughout; neither YES nor NO ever fills."""

    def _flat_market(self, mid: float = 0.50, n: int = 150) -> MarketHistory:
        # 150 × 10-min ticks = 25 hours, price flat at mid
        return _make_market([mid] * n, interval_s=600)

    def test_no_fills_when_price_flat(self):
        market = self._flat_market(0.50)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.yes_fills == 0
        assert result.no_fills  == 0

    def test_all_periods_are_scenario_1(self):
        market = self._flat_market(0.50)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.periods_one_filled  == 0
        assert result.periods_both_filled == 0
        assert result.periods_neither_filled > 0

    def test_reward_income_positive(self):
        market = self._flat_market(0.50, n=200)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.total_reward_income > 0

    def test_fill_pnl_is_zero(self):
        market = self._flat_market(0.50, n=200)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.total_fill_pnl == pytest.approx(0.0)

    def test_net_pnl_equals_rewards(self):
        market = self._flat_market(0.50, n=200)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.net_pnl == pytest.approx(result.total_reward_income, rel=1e-6)

    def test_longer_market_earns_more_rewards(self):
        short = _make_market([0.50] * 60)
        long  = _make_market([0.50] * 200)
        engine = BacktestEngine(_default_cfg())
        r_short = engine.run(short)
        r_long  = engine.run(long)
        assert r_long.total_reward_income > r_short.total_reward_income

    def test_rewards_scale_with_position_size(self):
        market = self._flat_market()
        r100 = BacktestEngine(_default_cfg(position_size=100)).run(market)
        r200 = BacktestEngine(_default_cfg(position_size=200)).run(market)
        assert r200.total_reward_income == pytest.approx(r100.total_reward_income * 2, rel=1e-3)

    def test_rewards_scale_with_daily_pool(self):
        market = self._flat_market()
        r_lo = BacktestEngine(_default_cfg(assumed_daily_pool_per_1k=0.10)).run(market)
        r_hi = BacktestEngine(_default_cfg(assumed_daily_pool_per_1k=0.40)).run(market)
        assert r_hi.total_reward_income > r_lo.total_reward_income


# ---------------------------------------------------------------------------
# Scenario 2: one side fills, immediate hedge
# ---------------------------------------------------------------------------

class TestScenario2OneSideFills:
    """Price drops sharply mid-market → YES fills, hedge is placed."""

    def _yes_fill_market(self) -> MarketHistory:
        # Drop happens INSIDE period 10 (ticks 54-59): period opens at 0.50
        # (yes_bid=0.48) and tick 55 onward is 0.40 → period_low=0.40 ≤ 0.48 → YES fills
        prices = [0.50] * 55 + [0.40] * 65
        return _make_market(prices, interval_s=600, resolved_yes=False)

    def _no_fill_market(self) -> MarketHistory:
        # Rise happens INSIDE period 10: opens at 0.50 (no_bid=0.48),
        # tick 55 → 0.60; 0.60 ≥ (1 - 0.48) = 0.52 → NO fills
        prices = [0.50] * 55 + [0.60] * 65
        return _make_market(prices, interval_s=600, resolved_yes=True)

    def test_yes_fills_when_price_drops(self):
        market = self._yes_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.yes_fills > 0

    def test_no_fills_when_price_rises(self):
        market = self._no_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.no_fills > 0

    def test_hedge_costs_within_max_fill_cost(self):
        market = self._yes_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        for p in result.periods:
            if p.yes_filled and not p.no_filled:
                combined = p.yes_bid + p.hedge_price
                assert combined <= engine.cfg.max_fill_cost + 1e-6

    def test_scenario_2_periods_counted(self):
        market = self._yes_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.periods_one_filled > 0

    def test_fill_pnl_uses_existing_no_order_as_hedge(self):
        """
        When YES fills, the hedge is the existing NO order at no_bid (maker).
        Combined = yes_bid + no_bid ≤ max_fill_cost.
        Gross PnL = (1.0 − combined) − taker_fee.
        With mid=0.50, yes_bid=no_bid=0.48, combined=0.96 → gross=$0.04.
        After 1% taker fee on $100 position: net = $0.03 > 0.
        """
        market = self._yes_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        for p in result.periods:
            if p.yes_filled and not p.no_filled:
                # Hedge price must equal the original no_bid
                assert p.hedge_price == pytest.approx(p.no_bid, abs=1e-6)
                # Combined cost must be ≤ max_fill_cost
                assert p.yes_bid + p.no_bid <= engine.cfg.max_fill_cost + 1e-6
                # Gross pnl = (1 - combined) - fee; should be > 0 for good markets
                scale = engine.cfg.position_size / 100
                expected = (1.0 - (p.yes_bid + p.no_bid) - engine.cfg.taker_fee) * scale
                assert p.fill_pnl == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Scenario 3: both sides fill
# ---------------------------------------------------------------------------

class TestScenario3BothFill:
    """Price swings far enough to trigger both YES and NO fills in one period."""

    def _both_fill_market(self) -> MarketHistory:
        # Extreme swing: drops to 0.40 then rises to 0.60 within same period
        # YES bid ≈ 0.48, NO bid ≈ 0.48 → YES triggers at 0.48, NO at 1-0.48=0.52
        n_per_period = 6   # ticks per 60-min period at 10-min spacing
        volatile = [0.50, 0.40, 0.50, 0.60, 0.50, 0.50]  # both extremes in one period
        prices = [0.50] * 6 + volatile + [0.50] * 6
        return _make_market(prices, interval_s=600)

    def test_both_filled_detected(self):
        market = self._both_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.periods_both_filled > 0

    def test_both_fill_pnl_positive(self):
        """1.00 - (yes_bid + no_bid) should be > 0."""
        market = self._both_fill_market()
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        for p in result.periods:
            if p.yes_filled and p.no_filled:
                assert p.fill_pnl > 0


# ---------------------------------------------------------------------------
# MarketHistory helpers
# ---------------------------------------------------------------------------

class TestMarketHistory:
    def test_span_hours_correct(self):
        # 120 ticks at 10-min = 1190 minutes ≈ 19.8h
        market = _make_market([0.5] * 120, interval_s=600)
        assert market.span_hours == pytest.approx(119 * 600 / 3600, rel=0.01)

    def test_time_near_50_all_at_mid(self):
        market = _make_market([0.50] * 50)
        assert market.time_near_50() == pytest.approx(1.0)

    def test_time_near_50_none_near(self):
        market = _make_market([0.10] * 50)
        assert market.time_near_50() == pytest.approx(0.0)

    def test_time_near_50_partial(self):
        prices = [0.50] * 50 + [0.10] * 50
        market = _make_market(prices)
        assert market.time_near_50() == pytest.approx(0.5, abs=0.01)

    def test_price_series_returns_all_prices(self):
        prices = [0.3, 0.4, 0.5, 0.6, 0.7]
        market = _make_market(prices)
        assert market.price_series == prices

    def test_empty_market_has_zero_span(self):
        market = MarketHistory(
            condition_id="", question="", yes_token_id="", no_token_id="",
            start_date="", end_date="", ticks=[], resolved_yes=False,
        )
        assert market.span_hours == 0.0


# ---------------------------------------------------------------------------
# Portfolio / multi-market
# ---------------------------------------------------------------------------

class TestPortfolioBacktest:
    def _make_portfolio(self, n: int = 4) -> list[MarketHistory]:
        markets = []
        for i in range(n):
            prices = [0.50 + 0.01 * (i % 3 - 1)] * 100
            m = _make_market(prices, question=f"Market {i}?")
            markets.append(m)
        return markets

    def test_portfolio_aggregates_correctly(self):
        markets = self._make_portfolio(4)
        portfolio = run_backtest(markets, _default_cfg())
        total_reward = sum(r.total_reward_income for r in portfolio.market_results)
        assert portfolio.total_reward_income == pytest.approx(total_reward, rel=1e-6)

    def test_net_pnl_positive_for_flat_markets(self):
        # Flat markets → only rewards, no fills → positive net PnL
        markets = self._make_portfolio(3)
        portfolio = run_backtest(markets, _default_cfg())
        assert portfolio.net_pnl > 0

    def test_per_market_results_count(self):
        markets = self._make_portfolio(5)
        portfolio = run_backtest(markets, _default_cfg())
        assert len(portfolio.market_results) == 5

    def test_portfolio_summary_is_string(self):
        markets = self._make_portfolio(2)
        portfolio = run_backtest(markets, _default_cfg())
        s = portfolio.portfolio_summary()
        assert isinstance(s, str)
        assert "Net P&L" in s


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_market_runs_without_error(self):
        market = MarketHistory(
            condition_id="empty", question="Empty?",
            yes_token_id="y", no_token_id="n",
            start_date="", end_date="", ticks=[], resolved_yes=True,
        )
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.num_periods == 0
        assert result.net_pnl == 0.0

    def test_single_tick_market(self):
        market = _make_market([0.50])
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        assert result.net_pnl >= 0

    def test_price_at_extremes_no_crash(self):
        prices = [0.01] * 30 + [0.99] * 30
        market = _make_market(prices)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        # Should not raise; extreme prices just trigger fills
        assert result is not None

    def test_combined_cost_always_within_max(self):
        """YES bid + NO bid must never exceed max_fill_cost."""
        for mid in [0.10, 0.30, 0.50, 0.70, 0.90]:
            market = _make_market([mid] * 50)
            engine = BacktestEngine(_default_cfg())
            result = engine.run(market)
            for p in result.periods:
                assert p.yes_bid + p.no_bid <= engine.cfg.max_fill_cost + 1e-6

    def test_varying_requote_intervals(self):
        market = _make_market([0.50] * 200, interval_s=60)
        for interval in [10, 30, 60, 120]:
            cfg = _default_cfg(requote_interval_min=interval)
            result = BacktestEngine(cfg).run(market)
            assert result.num_periods > 0

    def test_asymmetric_market_60_40(self):
        """Market at 0.60 should still produce valid results."""
        market = _make_market([0.60] * 100)
        engine = BacktestEngine(_default_cfg())
        result = engine.run(market)
        for p in result.periods:
            assert 0 < p.yes_bid < 1
            assert 0 < p.no_bid  < 1
            assert p.yes_bid + p.no_bid <= engine.cfg.max_fill_cost + 1e-6
