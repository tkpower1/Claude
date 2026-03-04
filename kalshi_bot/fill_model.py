"""
MLE-calibrated fill probability model for the backtester.

Problem the article highlighted
────────────────────────────────
The backtester used a hard-coded constant:

    hourly_rate = (snap.volume_usd / 100_000) * depth_penalty * 0.04

The 0.04 was a guess. With real fill data we can do better: fit a
logistic regression via Maximum Likelihood Estimation (MLE):

    P(fill in 1h) = sigmoid(β₀ + β₁·depth + β₂·log(volume) + β₃·spread)

This is the same MLE framework the article showed for fitting the
Student-t distribution, applied to binary (fill/no-fill) outcomes:

    θ̂_MLE = argmax Σᵢ [ yᵢ·ln(p̂ᵢ) + (1-yᵢ)·ln(1-p̂ᵢ) ]

Two operating modes
────────────────────
1. Calibrated (historical data available):
   Call FillModel.fit(records) where records = [(depth, log_vol, spread, filled), ...]
   Then FillModel.predict(depth, log_vol, spread) → P(fill in 1h)

2. Fallback (no data):
   Uses the original heuristic so the backtester always works out of the box.

Fitting from market_data.db
────────────────────────────
When fills are logged in state_store.db we can extract:
  - depth from order depth = (yes_price - mid) at quote time
  - log_vol from market_snapshots.volume_24h at quote time
  - spread from market_snapshots.spread at quote time
  - filled = 1 if the order was eventually filled, 0 otherwise

See FillModel.fit_from_db() for the full pipeline.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _log_loss(y: float, p_hat: float) -> float:
    """Binary cross-entropy for one observation."""
    p_hat = max(min(p_hat, 1 - 1e-9), 1e-9)
    return -(y * math.log(p_hat) + (1 - y) * math.log(1 - p_hat))


# ---------------------------------------------------------------------------
# MLE logistic regression (pure Python, no numpy/sklearn)
# ---------------------------------------------------------------------------

def _fit_logistic(
    X: list[list[float]],    # n × k feature matrix (include bias column)
    y: list[float],          # n binary labels
    lr: float = 0.05,
    max_iter: int = 1000,
    tol: float = 1e-6,
) -> list[float]:
    """
    Fit logistic regression by gradient descent on log-likelihood.

    This maximizes:
        ℓ(β) = Σᵢ [ yᵢ·ln(σ(Xᵢβ)) + (1-yᵢ)·ln(1-σ(Xᵢβ)) ]

    Returns coefficient vector β of length k.
    """
    n = len(y)
    k = len(X[0])
    beta = [0.0] * k
    prev_loss = float("inf")

    for _ in range(max_iter):
        # Forward pass: compute predictions
        preds = [_sigmoid(sum(X[i][j] * beta[j] for j in range(k))) for i in range(n)]

        # Loss (negative log-likelihood)
        loss = sum(_log_loss(y[i], preds[i]) for i in range(n)) / n

        if abs(prev_loss - loss) < tol:
            break
        prev_loss = loss

        # Gradient: ∂ℓ/∂β = Xᵀ(y - p̂) / n
        grad = [0.0] * k
        for i in range(n):
            residual = y[i] - preds[i]
            for j in range(k):
                grad[j] += X[i][j] * residual
        for j in range(k):
            grad[j] /= n

        # Gradient ascent (maximizing log-likelihood)
        beta = [beta[j] + lr * grad[j] for j in range(k)]

    return beta


# ---------------------------------------------------------------------------
# Fill model
# ---------------------------------------------------------------------------

@dataclass
class FillModelParams:
    """
    Fitted logistic regression coefficients.

    Default values are calibrated to reproduce the original heuristic:
      hourly_rate ≈ (volume / 100k) × depth_penalty × 0.04

    Verification at representative conditions:
      depth=0.03, vol=10 000 USD, spread=0.08 → P(fill) ≈ 0.65% / hr
      depth=0.00, vol=100 000 USD, spread=0.15 → P(fill) ≈ 2.0% / hr
      depth=0.10, vol= 10 000 USD, spread=0.08 → P(fill) ≈ 0.23% / hr
    """
    intercept: float = -7.0          # β₀ — low baseline (rare fills by default)
    coef_depth: float = -15.0        # β₁ — deeper → significantly lower P(fill)
    coef_log_vol: float = 0.25       # β₂ — more volume → modestly higher P(fill)
    coef_spread: float = 1.5         # β₃ — wider spread → higher P(fill)
    fitted: bool = False             # True when coefficients come from real data


class FillModel:
    """
    Logistic model for P(order fills within one hour).

    Default parameters are calibrated to reproduce the original heuristic:
      hourly_rate ≈ (volume / 100k) * depth_penalty * 0.04
    at typical market conditions (volume=$5k, spread=0.08, depth=0.02).

    After calling fit() or fit_from_db(), the model uses MLE-estimated
    coefficients from historical data.
    """

    def __init__(self, params: Optional[FillModelParams] = None) -> None:
        self.params = params or FillModelParams()

    def predict(
        self,
        depth: float,       # distance from our limit to the best ask (prob units)
        volume_usd: float,  # 24h volume in USD
        spread: float,      # current bid-ask spread (prob units)
    ) -> float:
        """Return P(fill within 1 hour), capped at 0.25."""
        p = self.params
        log_vol = math.log1p(max(volume_usd, 0.0))
        z = (
            p.intercept
            + p.coef_depth   * depth
            + p.coef_log_vol * log_vol
            + p.coef_spread  * spread
        )
        return min(_sigmoid(z), 0.25)

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, records: list[tuple[float, float, float, float]]) -> None:
        """
        Fit model from a list of (depth, volume_usd, spread, filled) tuples.

        filled = 1.0 if the order was eventually filled, 0.0 otherwise.

        Minimum 20 observations with at least one fill and one non-fill.
        Prints a warning and keeps default params if data is insufficient.
        """
        if len(records) < 20:
            logger.warning(
                "FillModel.fit: only %d records (need ≥20) – keeping defaults.",
                len(records),
            )
            return

        fills = sum(1 for *_, f in records if f > 0.5)
        if fills == 0 or fills == len(records):
            logger.warning(
                "FillModel.fit: all labels are %s – keeping defaults.",
                "filled" if fills else "not filled",
            )
            return

        # Build feature matrix (bias, depth, log_vol, spread)
        X, y = [], []
        for depth, vol, spread, filled in records:
            log_vol = math.log1p(max(vol, 0.0))
            X.append([1.0, depth, log_vol, spread])
            y.append(float(filled))

        beta = _fit_logistic(X, y)
        self.params = FillModelParams(
            intercept=beta[0],
            coef_depth=beta[1],
            coef_log_vol=beta[2],
            coef_spread=beta[3],
            fitted=True,
        )
        logger.info(
            "FillModel fitted: intercept=%.3f depth=%.3f log_vol=%.3f spread=%.3f  "
            "(n=%d, fill_rate=%.1f%%)",
            *beta, len(records), fills / len(records) * 100,
        )

    def fit_from_db(self, state_db: str, market_db: str) -> None:
        """
        Attempt to fit the model from historical data in state_store.db
        joined with market_data.db snapshots.

        Requires:
          - state_db: positions table with yes_price, no_price, last_quote_time,
                      filled_side, state
          - market_db: market_snapshots table

        Falls back to default parameters if tables are missing or empty.
        """
        try:
            records = _extract_fill_records(state_db, market_db)
        except Exception as exc:
            logger.warning("fit_from_db error: %s – using defaults.", exc)
            return

        if records:
            self.fit(records)
        else:
            logger.info("fit_from_db: no matching records found – using defaults.")


# ---------------------------------------------------------------------------
# DB extraction helper
# ---------------------------------------------------------------------------

def _extract_fill_records(
    state_db: str, market_db: str
) -> list[tuple[float, float, float, float]]:
    """
    Join positions (state_db) with snapshots (market_db) to build training data.

    For each position:
      - depth = distance from our yes_price to the mid at quote time
      - volume_usd = 24h volume at quote time (from nearest snapshot)
      - spread = spread at quote time
      - filled = 1 if state in (YES_FILLED, NO_FILLED, BOTH_FILLED, ONE_SIDE_HEDGED, RESOLVED)
                 0 if state = IDLE (stale cancel, never filled)
    """
    records: list[tuple[float, float, float, float]] = []

    with sqlite3.connect(state_db) as sconn, sqlite3.connect(market_db) as mconn:
        positions = sconn.execute(
            """SELECT ticker, yes_price, no_price, last_quote_time, state
               FROM positions"""
        ).fetchall()

        for ticker, yes_price, no_price, qt, state in positions:
            if yes_price is None or no_price is None or qt is None:
                continue

            # Look up the nearest market snapshot
            snap = mconn.execute(
                """SELECT mid, volume_24h, spread
                   FROM market_snapshots
                   WHERE ticker = ? AND ABS(ts - ?) < 300
                   ORDER BY ABS(ts - ?) ASC
                   LIMIT 1""",
                (ticker, int(qt), int(qt)),
            ).fetchone()

            if snap is None:
                continue

            mid, volume, spread = snap
            depth = abs(yes_price - mid)
            filled = 1.0 if state in (
                "YES_FILLED", "NO_FILLED", "BOTH_FILLED",
                "ONE_SIDE_HEDGED", "RESOLVED",
            ) else 0.0

            records.append((depth, volume or 0.0, spread or 0.0, filled))

    return records


# ---------------------------------------------------------------------------
# Module-level singleton (shared by backtester)
# ---------------------------------------------------------------------------

DEFAULT_FILL_MODEL = FillModel()
