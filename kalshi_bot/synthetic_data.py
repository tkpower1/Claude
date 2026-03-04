"""
Synthetic Kalshi market data generator for backtesting.

Generates realistic price paths and order-book snapshots using:
  - Geometric Brownian Motion (GBM) reflected onto (0, 1) for YES probability
  - Ornstein-Uhlenbeck mean reversion (markets tend to stay near their initial
    probability until new information arrives)
  - Stochastic spread: spread widens in volatile periods, narrows when calm
  - Fill simulation: an order fills when the market bid/ask crosses our limit

Calibration targets (from Kalshi public stats, 2023-2024):
  - Median bid-ask spread for $10k+ OI markets:  ~4-8¢
  - Typical daily volatility (σ):                 3-8% of probability
  - Tick size:                                     1¢  (0.01 in prob units)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterator


# ---------------------------------------------------------------------------
# A single market snapshot
# ---------------------------------------------------------------------------

@dataclass
class MarketSnapshot:
    """Order-book state at one point in time."""
    ticker: str
    t: float          # simulation time in days
    yes_bid: float    # probability 0-1
    yes_ask: float
    mid: float
    spread: float
    volume_usd: float
    open_interest: float


# ---------------------------------------------------------------------------
# Price-path generator
# ---------------------------------------------------------------------------

class PricePath:
    """
    Generates a sequence of (mid, spread) snapshots for a single binary market.

    Process:
      mid(t)    ~ reflected OU: dX = κ(μ - X)dt + σ dW, reflected to (0,1)
      spread(t) ~ log-normal noise around a base spread, correlated with |dX|
    """

    def __init__(
        self,
        ticker: str,
        initial_mid: float = 0.50,
        mu: float = 0.50,          # long-run mean (prior probability)
        kappa: float = 0.5,        # mean-reversion speed (per day)
        sigma: float = 0.05,       # daily volatility (probability units)
        base_spread: float = 0.06, # typical spread (probability units)
        spread_vol: float = 0.15,  # spread volatility (log-normal σ per sqrt-day)
        dt: float = 1 / 24,        # time step = 1 hour
        seed: int | None = None,
    ) -> None:
        self.ticker = ticker
        self.mu = mu
        self.kappa = kappa
        self.sigma = sigma
        self.base_spread = base_spread
        self.spread_vol = spread_vol
        self.dt = dt
        self._mid = initial_mid
        self._spread = base_spread
        self._t = 0.0
        self._rng = random.Random(seed)

    @property
    def t(self) -> float:
        return self._t

    def step(self) -> tuple[float, float]:
        """Advance one time step. Returns (new_mid, new_spread)."""
        dt = self.dt
        sqrt_dt = math.sqrt(dt)
        rng = self._rng

        # OU step for mid
        dW = rng.gauss(0, 1)
        drift = self.kappa * (self.mu - self._mid) * dt
        diffusion = self.sigma * sqrt_dt * dW
        new_mid = self._mid + drift + diffusion

        # Reflect off boundaries to stay in (0.01, 0.99)
        if new_mid < 0.01:
            new_mid = 0.02 - new_mid
        elif new_mid > 0.99:
            new_mid = 1.98 - new_mid
        new_mid = max(0.01, min(0.99, new_mid))

        # Spread: Ornstein-Uhlenbeck process in log-space.
        #
        # d log(s) = κ_s · (log(base_spread) - log(s)) · dt + σ_s · dW_s
        #
        # This mean-reverts to base_spread without exploding.  An additive
        # widening term proportional to the price move captures the adverse-
        # selection effect (large moves → temporarily wider spreads) without
        # the old 1+3|dW| multiplicative bias that caused spreads to pin at 0.30.
        #
        # Calibration: κ_s=4/day → half-life ≈ 4h; σ_s=0.15 keeps the spread
        # mostly within ±1.5 base_spread, consistent with Kalshi market data.
        kappa_s = 4.0  # mean-reversion speed for spread (per day)
        log_s = math.log(max(self._spread, 0.001))
        log_s_new = (
            log_s
            + kappa_s * (math.log(self.base_spread) - log_s) * dt
            + self.spread_vol * sqrt_dt * rng.gauss(0, 1)
        )
        new_spread = math.exp(log_s_new)
        # Small additive widening on large price moves (adverse selection)
        new_spread += 0.5 * abs(diffusion)
        # Keep spread between 0.01 and 0.30
        new_spread = max(0.01, min(0.30, new_spread))
        # Spread can't push prices outside (0, 1)
        max_allowed_spread = 2 * min(new_mid, 1.0 - new_mid) - 0.01
        new_spread = min(new_spread, max(max_allowed_spread, 0.01))

        self._mid = new_mid
        self._spread = new_spread
        self._t += dt
        return new_mid, new_spread

    def snapshot(self) -> MarketSnapshot:
        mid = self._mid
        half = self._spread / 2
        yes_bid = round(max(mid - half, 0.01), 2)
        yes_ask = round(min(mid + half, 0.99), 2)
        actual_spread = yes_ask - yes_bid

        return MarketSnapshot(
            ticker=self.ticker,
            t=self._t,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            mid=(yes_bid + yes_ask) / 2,
            spread=actual_spread,
            # 24-hour volume (USD).  Tighter spread → more active market → higher volume.
            # Formula: mean_24h = 120_000 / (spread_pct) where spread_pct = spread * 100.
            # At spread=0.06 (6¢) → mean ≈ $20k/day.  At spread=0.12 → ≈$10k/day.
            # Exponential distribution models the heavy tail of daily volume.
            volume_usd=self._rng.expovariate(
                1 / max(120_000 / max(actual_spread * 100, 0.5), 1)
            ),
            open_interest=self._rng.uniform(1_000, 50_000),
        )

    def generate(self, days: float) -> list[MarketSnapshot]:
        """Generate `days` worth of hourly snapshots."""
        steps = int(days / self.dt)
        snapshots: list[MarketSnapshot] = []
        for _ in range(steps):
            self.step()
            snapshots.append(self.snapshot())
        return snapshots


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    description: str
    days: float
    markets: list[dict]   # kwargs for PricePath(ticker=..., ...)


# Pre-built stress scenarios
SCENARIOS: list[Scenario] = [
    Scenario(
        name="calm_50_50",
        description="Calm near-50 markets – ideal for market-making",
        days=30,
        markets=[
            dict(ticker="CALM-A", initial_mid=0.50, sigma=0.02, base_spread=0.06),
            dict(ticker="CALM-B", initial_mid=0.48, sigma=0.02, base_spread=0.05),
            dict(ticker="CALM-C", initial_mid=0.52, sigma=0.03, base_spread=0.07),
        ],
    ),
    Scenario(
        name="trending_adverse",
        description="Markets drift away from 50/50 (directional risk)",
        days=30,
        markets=[
            dict(ticker="TREND-A", initial_mid=0.50, mu=0.80, kappa=1.5, sigma=0.06),
            dict(ticker="TREND-B", initial_mid=0.50, mu=0.20, kappa=1.5, sigma=0.06),
            dict(ticker="TREND-C", initial_mid=0.48, mu=0.75, kappa=1.0, sigma=0.05),
        ],
    ),
    Scenario(
        name="high_volatility",
        description="High-volatility markets with frequent price swings",
        days=14,
        markets=[
            dict(ticker="HVOL-A", initial_mid=0.50, sigma=0.12, base_spread=0.10),
            dict(ticker="HVOL-B", initial_mid=0.47, sigma=0.10, base_spread=0.09),
            dict(ticker="HVOL-C", initial_mid=0.53, sigma=0.11, base_spread=0.08),
        ],
    ),
    Scenario(
        name="wide_spread",
        description="Wide spreads – high per-pair profit if both fill",
        days=30,
        markets=[
            dict(ticker="WIDE-A", initial_mid=0.50, sigma=0.04, base_spread=0.15),
            dict(ticker="WIDE-B", initial_mid=0.49, sigma=0.03, base_spread=0.14),
            dict(ticker="WIDE-C", initial_mid=0.51, sigma=0.04, base_spread=0.16),
        ],
    ),
    Scenario(
        name="tight_spread",
        description="Tight spreads – harder to profit, more fills",
        days=30,
        markets=[
            dict(ticker="TIGHT-A", initial_mid=0.50, sigma=0.03, base_spread=0.02),
            dict(ticker="TIGHT-B", initial_mid=0.50, sigma=0.02, base_spread=0.02),
            dict(ticker="TIGHT-C", initial_mid=0.51, sigma=0.03, base_spread=0.02),
        ],
    ),
    Scenario(
        name="extreme_tails",
        description="Markets near extremes (high directional risk)",
        days=21,
        markets=[
            dict(ticker="TAIL-A", initial_mid=0.35, mu=0.35, sigma=0.04, base_spread=0.05),
            dict(ticker="TAIL-B", initial_mid=0.65, mu=0.65, sigma=0.04, base_spread=0.05),
            dict(ticker="TAIL-C", initial_mid=0.30, mu=0.30, sigma=0.05, base_spread=0.06),
        ],
    ),
    Scenario(
        name="mixed_portfolio",
        description="Realistic mix of market conditions",
        days=30,
        markets=[
            dict(ticker="MIX-A", initial_mid=0.50, sigma=0.03, base_spread=0.06),
            dict(ticker="MIX-B", initial_mid=0.45, mu=0.65, kappa=0.8, sigma=0.05),
            dict(ticker="MIX-C", initial_mid=0.55, sigma=0.08, base_spread=0.10),
            dict(ticker="MIX-D", initial_mid=0.38, mu=0.38, sigma=0.04, base_spread=0.05),
            dict(ticker="MIX-E", initial_mid=0.50, sigma=0.15, base_spread=0.12),
        ],
    ),
    Scenario(
        name="black_swan",
        description="Sudden resolution shock (price jumps to 0 or 1)",
        days=7,
        markets=[
            dict(ticker="SWAN-A", initial_mid=0.50, sigma=0.05, base_spread=0.06),
            dict(ticker="SWAN-B", initial_mid=0.50, sigma=0.05, base_spread=0.06),
        ],
    ),
]
