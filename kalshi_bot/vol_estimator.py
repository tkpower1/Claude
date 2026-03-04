"""
Realized volatility estimator for adaptive order-depth sizing.

The core insight from stochastic calculus (Black-Scholes / Itô):
  - The relevant uncertainty is σ (volatility of the underlying price process)
  - Quoting deeper inside a wide spread when σ is HIGH reduces adverse selection
  - Quoting tighter when σ is LOW improves fill probability

We estimate realized volatility as:

    σ_daily = std(Δmid) × √(24 / dt_hours)

where Δmid = consecutive changes in the mid-price (in probability units).
This is the discrete analogue of the quadratic variation estimator used in
stochastic calculus, applied to the binary probability process rather than
log-prices (since probabilities are already on [0,1]).

Two entry points:
  1. realized_vol(mids, dt_hours)       — pure-Python, used by backtester
  2. realized_vol_from_db(ticker, ...)  — SQLite-backed, used by live bot
"""

from __future__ import annotations

import math
import sqlite3
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Core estimator (pure Python, no I/O)
# ---------------------------------------------------------------------------

def realized_vol(
    mids: list[float],
    dt_hours: float = 1.0,
    min_obs: int = 4,
) -> Optional[float]:
    """
    Estimate daily realized volatility from a sequence of mid-prices.

    Parameters
    ----------
    mids      : list of mid-prices in chronological order (probability units)
    dt_hours  : time between observations in hours (default: 1h collector cadence)
    min_obs   : minimum observations required; returns None if fewer are available

    Returns
    -------
    σ_daily   : annualized (daily) volatility in probability units, or None.
    """
    if len(mids) < min_obs:
        return None

    changes = [mids[i + 1] - mids[i] for i in range(len(mids) - 1)]
    n = len(changes)
    mean = sum(changes) / n
    variance = sum((c - mean) ** 2 for c in changes) / max(n - 1, 1)
    std_per_step = math.sqrt(variance)

    # Scale to daily units: σ_daily = σ_step × √(steps_per_day)
    steps_per_day = 24.0 / max(dt_hours, 1.0 / 60.0)
    return std_per_step * math.sqrt(steps_per_day)


# ---------------------------------------------------------------------------
# Database-backed lookup (used by live bot)
# ---------------------------------------------------------------------------

def realized_vol_from_db(
    ticker: str,
    db_path: str,
    lookback_hours: int = 24,
    min_obs: int = 6,
) -> Optional[float]:
    """
    Query recent snapshots from market_data.db and compute realized volatility.

    Returns None if the database is unavailable or there is insufficient history.
    Falls back gracefully — callers should use config.scoring.default_v when None.
    """
    cutoff = int(time.time()) - lookback_hours * 3600
    try:
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            rows = conn.execute(
                """SELECT ts, mid
                   FROM market_snapshots
                   WHERE ticker = ? AND ts >= ?
                   ORDER BY ts ASC""",
                (ticker, cutoff),
            ).fetchall()
    except Exception:
        return None

    if len(rows) < min_obs:
        return None

    mids = [r[1] for r in rows]
    timestamps = [r[0] for r in rows]

    # Estimate mean dt from actual timestamps (may not be uniform)
    total_secs = timestamps[-1] - timestamps[0]
    dt_hours = (total_secs / (len(timestamps) - 1)) / 3600.0 if len(timestamps) > 1 else 1.0

    return realized_vol(mids, dt_hours=dt_hours, min_obs=min_obs)


# ---------------------------------------------------------------------------
# Effective volatility: realized (if available) else config default
# ---------------------------------------------------------------------------

def effective_vol(
    ticker: str,
    market_spread: float,
    default_v: float,
    data_db: Optional[str] = None,
    lookback_hours: int = 24,
) -> tuple[float, str]:
    """
    Return the volatility estimate to use for depth sizing, plus a source label.

    Priority:
      1. Realized vol from DB   (source="realized")
      2. Current market spread  (source="spread")    — spread is a vol proxy
      3. config.scoring.default_v (source="default")

    The spread is included as a fallback because a wide spread implies market
    makers are pricing in uncertainty (adverse selection risk).
    """
    if data_db:
        rv = realized_vol_from_db(ticker, data_db, lookback_hours=lookback_hours)
        if rv is not None and rv > 0:
            return rv, "realized"

    # Spread as vol proxy: at 1-hour cadence, a 6¢ spread ≈ 1.5¢/hr move expectation
    spread_vol = market_spread / 4.0  # rough: spread ≈ 4× hourly σ
    if spread_vol > 0:
        return spread_vol, "spread"

    return default_v, "default"
