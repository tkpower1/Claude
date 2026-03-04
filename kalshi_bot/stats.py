"""
Statistical significance testing for backtester P&L series.

Why Newey-West (HAC)?
─────────────────────
The article makes this explicit: market-making P&L has both autocorrelation
(consecutive positions on the same market) and heteroskedasticity (volatility
clustering). Standard OLS t-statistics are biased in this case.

The Newey-West (1987) HAC estimator corrects the variance:

    Var_HAC(β̂) = (XᵀX)⁻¹ · Ω_HAC · (XᵀX)⁻¹

where Ω_HAC = S₀ + Σₗ₌₁ᴸ (1 - l/(L+1)) · (Sₗ + Sₗᵀ)

For our use case (testing whether mean P&L ≠ 0):
    β̂ = mean(pnl_series)
    XᵀX = n
    Ω_HAC = n² · (1/n)² · Σ of Bartlett-weighted autocovariances
          = Σₗ₌₋ᴸᴸ w(l,L) · γ(l)

where γ(l) = sample autocovariance at lag l
      w(l,L) = 1 - |l|/(L+1)   (Bartlett kernel)
      L = bandwidth (rule-of-thumb: 4·(n/100)^(2/9))

Result: t-statistic = mean(pnl) / sqrt(var_HAC / n)

This is pure-Python, no numpy required. Works on lists of floats.

References:
  Newey, Whitney K. and Kenneth D. West (1987).
  "A Simple, Positive Semi-Definite, Heteroskedasticity and
   Autocorrelation Consistent Covariance Matrix."
  Econometrica, 55(3), 703-708.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class TTestResult:
    mean: float
    std: float           # standard deviation of the series
    se_newey_west: float # Newey-West (HAC) standard error of the mean
    t_stat: float        # t = mean / se_newey_west
    n: int               # number of observations
    bandwidth: int       # Newey-West bandwidth L used

    @property
    def significant_5pct(self) -> bool:
        """Two-sided 5% significance (approximate: |t| > 1.96)."""
        return abs(self.t_stat) > 1.96

    @property
    def significant_10pct(self) -> bool:
        return abs(self.t_stat) > 1.645

    def summary(self) -> str:
        stars = ""
        if abs(self.t_stat) > 2.576:
            stars = " ***"
        elif abs(self.t_stat) > 1.96:
            stars = " **"
        elif abs(self.t_stat) > 1.645:
            stars = " *"
        return (
            f"mean={self.mean:+.4f}  SE_NW={self.se_newey_west:.4f}"
            f"  t={self.t_stat:+.2f}{stars}  n={self.n}"
        )


def newey_west_ttest(series: list[float], bandwidth: int | None = None) -> TTestResult:
    """
    Test H₀: mean(series) = 0 using Newey-West HAC standard errors.

    Parameters
    ----------
    series    : list of P&L observations (one per closed position)
    bandwidth : Newey-West lag truncation (default: rule-of-thumb 4·(n/100)^2/9)

    Returns
    -------
    TTestResult with t-statistic, SE, and significance flags.
    """
    n = len(series)
    if n < 2:
        return TTestResult(
            mean=series[0] if series else 0.0,
            std=0.0, se_newey_west=0.0, t_stat=0.0, n=n, bandwidth=0,
        )

    # Mean and demeaned series
    mu = sum(series) / n
    u = [x - mu for x in series]

    # Bandwidth rule-of-thumb: L = floor(4 · (n/100)^(2/9))
    if bandwidth is None:
        bandwidth = max(1, int(4 * (n / 100) ** (2 / 9)))

    # Sample autocovariances  γ(l) = (1/n) Σₜ uₜ · uₜ₋ₗ
    def autocovariance(lag: int) -> float:
        total = sum(u[t] * u[t - lag] for t in range(lag, n))
        return total / n

    # Newey-West HAC variance of the mean:
    # V_HAC = γ(0) + 2 · Σₗ₌₁ᴸ (1 - l/(L+1)) · γ(l)
    gamma0 = autocovariance(0)
    v_hac = gamma0
    for lag in range(1, bandwidth + 1):
        weight = 1.0 - lag / (bandwidth + 1)
        v_hac += 2.0 * weight * autocovariance(lag)

    # Var(mean) = V_HAC / n
    var_mean = max(v_hac / n, 1e-12)
    se = math.sqrt(var_mean)
    t_stat = mu / se if se > 0 else 0.0

    std = math.sqrt(gamma0)  # sample std dev (γ(0) = sample variance)

    return TTestResult(
        mean=mu,
        std=std,
        se_newey_west=se,
        t_stat=t_stat,
        n=n,
        bandwidth=bandwidth,
    )


def pnl_ttest_from_results(market_results) -> "TTestResult | None":
    """
    Compute a Newey-West t-test on per-position P&L across all markets.

    market_results : list of MarketResult from the backtester.
    Returns None if there are no closed positions.

    Priority for P&L series construction:
      1. Per-position avg from BOTH_FILLED gross P&L (cleanest signal)
      2. Per-position avg from stop-loss / one-side exits (via gross_pnl)
      3. Per-market total_pnl / positions_opened (fallback)
    """
    pnl_series: list[float] = []
    for r in market_results:
        n_closed = r.positions_both_filled + r.positions_one_filled
        if n_closed > 0 and (r.gross_pnl != 0 or r.unrealised_pnl != 0):
            # Distribute total realised P&L evenly across closed positions
            total_realised = r.gross_pnl + r.unrealised_pnl
            avg = total_realised / n_closed
            pnl_series.extend([avg] * n_closed)
        elif r.positions_opened > 0:
            # Fallback: distribute total P&L across all opened positions
            avg = r.total_pnl / r.positions_opened
            pnl_series.extend([avg] * r.positions_opened)

    if not pnl_series:
        return None
    return newey_west_ttest(pnl_series)
