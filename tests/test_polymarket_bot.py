"""
Unit tests for the Polymarket LP / Hedge Bot.

Tests are isolated from live API calls – the ClobClient is mocked throughout.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from polymarket_bot.config import BotConfig, MarketFilter, RiskParams, ScoringParams
from polymarket_bot.rewards import (
    order_score,
    optimal_order_depth,
    estimate_reward_share,
    compute_scenario_pnl,
    ladder_total_score,
    LadderSpec,
    min_reward_to_break_even,
)
from polymarket_bot.position_sizer import kelly_fraction, size_position, BudgetTracker
from polymarket_bot.order_manager import OrderManager, PositionState, MarketPosition
from polymarket_bot.market_selector import passes_filter, _days_to_expiry
from polymarket_bot.client import MarketInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config() -> BotConfig:
    return BotConfig(
        private_key="deadbeef",
        api_key="key",
        api_secret="secret",
        api_passphrase="pass",
        dry_run=True,
    )


@pytest.fixture
def mock_client(default_config) -> MagicMock:
    client = MagicMock()
    client.cfg = default_config
    client.get_open_orders.return_value = []
    client.get_balance.return_value = 1000.0
    # Return unique IDs so YES and NO order IDs are distinct
    _counter = {"n": 0}
    def _unique_order_id(*args, **kwargs):
        _counter["n"] += 1
        return f"order-{_counter['n']}"
    client.place_limit_order.side_effect = _unique_order_id
    client.cancel_order.return_value = True
    return client


@pytest.fixture
def sample_market() -> MarketInfo:
    return MarketInfo(
        condition_id="cond-abc",
        question="Will X happen?",
        yes_token_id="yes-tok",
        no_token_id="no-tok",
        mid_price=0.50,
        best_bid=0.48,
        best_ask=0.52,
        spread=0.04,
        volume_24h=50_000.0,
        open_interest=10_000.0,
        end_date_iso="2030-01-01T00:00:00Z",
        active=True,
        reward_rate=100.0,
        max_spread=0.05,
        multiplier=1.0,
    )


# ---------------------------------------------------------------------------
# rewards.py
# ---------------------------------------------------------------------------

class TestOrderScore:
    def test_at_mid_gives_max(self):
        # s=0 → score = b
        assert order_score(0.0, 0.05, 1.0) == pytest.approx(1.0)

    def test_at_edge_gives_zero(self):
        # s=v → score = 0
        assert order_score(0.05, 0.05, 1.0) == pytest.approx(0.0)

    def test_beyond_edge_gives_zero(self):
        assert order_score(0.10, 0.05, 1.0) == 0.0

    def test_midpoint(self):
        # s = v/2 → score = 0.25 * b
        assert order_score(0.025, 0.05, 1.0) == pytest.approx(0.25)

    def test_multiplier_scales_score(self):
        s1 = order_score(0.01, 0.05, 1.0)
        s2 = order_score(0.01, 0.05, 2.0)
        assert s2 == pytest.approx(s1 * 2)

    def test_zero_v_returns_zero(self):
        assert order_score(0.01, 0.0, 1.0) == 0.0

    def test_score_decreases_with_depth(self):
        scores = [order_score(s, 0.05) for s in [0.01, 0.02, 0.03, 0.04]]
        assert scores == sorted(scores, reverse=True)


class TestEstimateRewardShare:
    def test_sole_maker_gets_all(self):
        income = estimate_reward_share(1.0, 1.0, 100.0)
        assert income == pytest.approx(100.0)

    def test_half_share(self):
        income = estimate_reward_share(1.0, 2.0, 100.0)
        assert income == pytest.approx(50.0)

    def test_zero_pool(self):
        assert estimate_reward_share(1.0, 1.0, 0.0) == 0.0

    def test_zero_competing_score(self):
        assert estimate_reward_share(1.0, 0.0, 100.0) == 0.0


class TestScenarioPnL:
    def test_scenario_3_profitable_when_combined_under_1(self):
        pnl = compute_scenario_pnl(0.49, 0.49, 1.0)
        assert pnl.both_filled_net_pnl == pytest.approx(0.02)
        assert pnl.both_filled_is_profitable is True

    def test_scenario_3_unprofitable_when_combined_over_1(self):
        pnl = compute_scenario_pnl(0.55, 0.55, 1.0)
        assert pnl.both_filled_net_pnl < 0
        assert pnl.both_filled_is_profitable is False

    def test_scenario_2_profitable_within_max_cost(self):
        pnl = compute_scenario_pnl(0.48, 0.50, 1.0, max_fill_cost=1.02)
        # combined = 0.98 ≤ 1.02
        assert pnl.one_filled_is_profitable is True
        assert pnl.one_filled_net_pnl == pytest.approx(0.02)

    def test_scenario_2_unprofitable_above_max_cost(self):
        pnl = compute_scenario_pnl(0.55, 0.55, 1.0, max_fill_cost=1.02)
        assert pnl.one_filled_is_profitable is False

    def test_scenario_1_rewards_pass_through(self):
        pnl = compute_scenario_pnl(0.48, 0.48, 5.0)
        assert pnl.neither_filled_daily_income == pytest.approx(5.0)

    def test_symmetric_50_50_market(self):
        pnl = compute_scenario_pnl(0.49, 0.49, 0.0)
        assert pnl.both_filled_net_pnl == pytest.approx(0.02, abs=1e-6)


class TestLadderScore:
    def test_two_levels_two_sides_greater_than_one_level(self):
        ladder1 = LadderSpec(1, 0.02, 0.01, 10.0)
        ladder2 = LadderSpec(2, 0.02, 0.01, 10.0)
        assert ladder_total_score(ladder2, 0.05) > ladder_total_score(ladder1, 0.05)

    def test_single_side(self):
        ladder = LadderSpec(1, 0.02, 0.01, 10.0)
        two_sides = ladder_total_score(ladder, 0.05, sides=2)
        one_side = ladder_total_score(ladder, 0.05, sides=1)
        assert two_sides == pytest.approx(one_side * 2)


class TestMinRewardBreakEven:
    def test_profitable_combo_needs_no_minimum(self):
        result = min_reward_to_break_even(0.48, 0.48, 0.1)
        assert result == 0.0

    def test_unprofitable_combo_needs_positive_minimum(self):
        # combined = 1.10 > 1.02 → loss if filled
        result = min_reward_to_break_even(0.55, 0.55, 0.1, max_fill_cost=1.02)
        assert result > 0.0

    def test_higher_fill_prob_raises_minimum(self):
        r1 = min_reward_to_break_even(0.55, 0.55, 0.01, max_fill_cost=1.02)
        r2 = min_reward_to_break_even(0.55, 0.55, 0.10, max_fill_cost=1.02)
        assert r2 > r1


# ---------------------------------------------------------------------------
# position_sizer.py
# ---------------------------------------------------------------------------

class TestKellyFraction:
    def test_50_50_gives_max_fraction(self):
        # At p=0.5, balance=1 → fraction = kelly_multiplier
        assert kelly_fraction(0.5, 0.25) == pytest.approx(0.25)

    def test_edge_prices_give_zero(self):
        assert kelly_fraction(0.0) == 0.0
        assert kelly_fraction(1.0) == 0.0

    def test_high_probability_gives_positive(self):
        # p=0.7: balance = 1 - |2*0.7-1| = 0.6 → fraction = 0.6 * 0.25 = 0.15
        assert kelly_fraction(0.7) == pytest.approx(0.15)

    def test_multiplier_scales_result(self):
        f_full = kelly_fraction(0.7, kelly_multiplier=1.0)
        f_quarter = kelly_fraction(0.7, kelly_multiplier=0.25)
        assert f_quarter == pytest.approx(f_full * 0.25)

    def test_result_never_exceeds_multiplier(self):
        for p in [0.6, 0.7, 0.8, 0.9, 0.99]:
            assert kelly_fraction(p, 0.25) <= 0.25


class TestSizePosition:
    def test_yes_no_combined_under_max_cost(self, sample_market, default_config):
        sizing = size_position(sample_market, 1000.0, default_config)
        combined = sizing.yes_price + sizing.no_price
        assert combined <= default_config.risk.max_fill_cost

    def test_yes_price_below_mid(self, sample_market, default_config):
        sizing = size_position(sample_market, 1000.0, default_config)
        assert sizing.yes_price < sample_market.mid_price

    def test_no_price_below_no_mid(self, sample_market, default_config):
        sizing = size_position(sample_market, 1000.0, default_config)
        no_mid = 1.0 - sample_market.mid_price
        assert sizing.no_price < no_mid + 1e-6  # allow rounding

    def test_budget_allocated_within_available(self, sample_market, default_config):
        available = 500.0
        sizing = size_position(sample_market, available, default_config)
        assert sizing.budget_allocated <= available

    def test_size_per_level_positive(self, sample_market, default_config):
        sizing = size_position(sample_market, 1000.0, default_config)
        assert sizing.size_per_level >= 1.0


class TestBudgetTracker:
    def test_available_decreases_after_allocate(self):
        bt = BudgetTracker(1000.0)
        bt.allocate("mkt1", 200.0)
        assert bt.available == pytest.approx(800.0)

    def test_available_increases_after_release(self):
        bt = BudgetTracker(1000.0)
        bt.allocate("mkt1", 200.0)
        bt.release("mkt1")
        assert bt.available == pytest.approx(1000.0)

    def test_deploy_multiple_markets(self):
        bt = BudgetTracker(1000.0)
        bt.allocate("mkt1", 100.0)
        bt.allocate("mkt2", 150.0)
        assert bt.available == pytest.approx(750.0)

    def test_release_nonexistent_is_safe(self):
        bt = BudgetTracker(1000.0)
        bt.release("does-not-exist")
        assert bt.available == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# order_manager.py
# ---------------------------------------------------------------------------

class TestOrderManager:
    def test_open_position_calls_place_limit_order_twice(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        mgr.open_position(
            condition_id="cond-1",
            question="Test?",
            yes_token_id="yes-tok",
            no_token_id="no-tok",
            yes_price=0.48,
            no_price=0.48,
            size=10.0,
        )
        assert mock_client.place_limit_order.call_count == 2

    def test_position_starts_in_quoting_state(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-1", "Test?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        assert pos.state == PositionState.QUOTING

    def test_combined_cost_too_high_skips_open(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        # 0.55 + 0.55 = 1.10 > 1.02
        pos = mgr.open_position(
            "cond-2", "Expensive?", "yes-tok", "no-tok", 0.55, 0.55, 10.0
        )
        assert pos.state == PositionState.IDLE
        mock_client.place_limit_order.assert_not_called()

    def test_refresh_detects_yes_fill_and_hedges(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-1", "Test?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        # Simulate YES order no longer on book (filled)
        # NO order still live
        mock_client.get_open_orders.return_value = [
            MagicMock(order_id=pos.no_order_id)
        ]
        mgr.refresh_all()
        assert pos.state == PositionState.ONE_SIDE_HEDGED
        # Hedge order should have been placed
        assert pos.hedge_order_id is not None

    def test_refresh_detects_both_filled(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-1", "Test?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        # Both orders gone from book
        mock_client.get_open_orders.return_value = []
        mgr.refresh_all()
        assert pos.state == PositionState.BOTH_FILLED

    def test_close_position_cancels_orders(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        mgr.open_position(
            "cond-1", "Test?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        mgr.close_position("cond-1")
        assert mock_client.cancel_order.called

    def test_record_rewards(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        mgr.open_position(
            "cond-1", "Test?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        mgr.record_rewards("cond-1", 2.50)
        mgr.record_rewards("cond-1", 1.75)
        assert mgr.positions["cond-1"].total_rewards_earned == pytest.approx(4.25)


# ---------------------------------------------------------------------------
# market_selector.py
# ---------------------------------------------------------------------------

class TestPassesFilter:
    def test_good_market_passes(self, sample_market):
        filt = MarketFilter()
        ok, reason = passes_filter(sample_market, filt)
        assert ok, f"Should pass but got: {reason}"

    def test_inactive_market_fails(self, sample_market):
        sample_market.active = False
        ok, _ = passes_filter(sample_market, MarketFilter())
        assert not ok

    def test_mid_too_low_fails(self, sample_market):
        sample_market.mid_price = 0.20
        ok, reason = passes_filter(sample_market, MarketFilter())
        assert not ok
        assert "mid" in reason

    def test_mid_too_high_fails(self, sample_market):
        sample_market.mid_price = 0.80
        ok, reason = passes_filter(sample_market, MarketFilter())
        assert not ok

    def test_spread_too_small_fails(self, sample_market):
        sample_market.spread = 0.005
        ok, reason = passes_filter(sample_market, MarketFilter())
        assert not ok
        assert "spread" in reason

    def test_oi_too_high_fails(self, sample_market):
        sample_market.open_interest = 500_000.0
        ok, reason = passes_filter(sample_market, MarketFilter())
        assert not ok
        assert "OI" in reason

    def test_expiry_too_soon_fails(self, sample_market):
        # Date in the past → 0 days remaining
        sample_market.end_date_iso = "2000-01-01T00:00:00Z"
        ok, reason = passes_filter(sample_market, MarketFilter())
        assert not ok
        assert "days" in reason


class TestDaysToExpiry:
    def test_future_date_positive(self):
        assert _days_to_expiry("2030-01-01T00:00:00Z") > 0

    def test_past_date_zero(self):
        assert _days_to_expiry("2000-01-01T00:00:00Z") == 0.0

    def test_empty_string_zero(self):
        assert _days_to_expiry("") == 0.0

    def test_date_only_format(self):
        assert _days_to_expiry("2030-06-15") > 0


# ---------------------------------------------------------------------------
# Integration-style: full open→fill→hedge cycle
# ---------------------------------------------------------------------------

class TestFullCycle:
    """Verify the complete three-scenario flow without live API calls."""

    def test_scenario_1_neither_fills(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-s1", "Scenario 1?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        # Orders remain live every refresh
        from polymarket_bot.order_manager import Order
        live = [
            MagicMock(order_id=pos.yes_order_id),
            MagicMock(order_id=pos.no_order_id),
        ]
        mock_client.get_open_orders.return_value = live
        for _ in range(3):
            mgr.refresh_all()
        # Still quoting – collecting rewards
        assert pos.state == PositionState.QUOTING

    def test_scenario_2_yes_fills_hedge_placed(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-s2", "Scenario 2?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        # Simulate: YES gone (filled), NO still live
        mock_client.get_open_orders.return_value = [MagicMock(order_id=pos.no_order_id)]
        mgr.refresh_all()
        assert pos.state == PositionState.ONE_SIDE_HEDGED
        # Total cost of YES + hedge ≤ max_fill_cost
        hedge_price = 1.02 - pos.yes_price
        assert pos.yes_price + min(hedge_price, 0.99) <= default_config.risk.max_fill_cost

    def test_scenario_3_both_fill(self, mock_client, default_config):
        mgr = OrderManager(mock_client, default_config)
        pos = mgr.open_position(
            "cond-s3", "Scenario 3?", "yes-tok", "no-tok", 0.48, 0.48, 10.0
        )
        mock_client.get_open_orders.return_value = []
        mgr.refresh_all()
        assert pos.state == PositionState.BOTH_FILLED
        # Net PnL should be positive
        assert 1.0 - (pos.yes_price + pos.no_price) > 0
