"""
Market selection: filter and rank Kalshi markets for the market-making strategy.

Selection criteria:
  1. Market status = "open" (active, accepting orders)
  2. YES mid-price near 50/50 (most balanced risk, widest spread opportunity)
  3. Minimum bid-ask spread >= min_spread (confirms gap exists)
  4. Low open interest (less competition)
  5. Sufficient time to resolution
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .client import KalshiClient, MarketInfo
from .config import BotConfig, MarketFilter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parse raw API response → MarketInfo
# ---------------------------------------------------------------------------

def _parse_market(raw: dict) -> Optional[MarketInfo]:
    """Convert a raw Kalshi market dict into a MarketInfo dataclass."""
    try:
        ticker = raw.get("ticker", "")
        title = raw.get("title", raw.get("question", "Unknown"))
        status = raw.get("status", "").lower()

        if not ticker:
            return None

        # Prices come as integer cents (0–100); convert to probability
        yes_bid_c = raw.get("yes_bid", 0) or 0
        yes_ask_c = raw.get("yes_ask", 100) or 100

        yes_bid = yes_bid_c / 100.0
        yes_ask = yes_ask_c / 100.0

        # NO prices are the complement
        no_bid = (100 - yes_ask_c) / 100.0
        no_ask = (100 - yes_bid_c) / 100.0

        mid_price = (yes_bid + yes_ask) / 2
        spread = yes_ask - yes_bid

        # Volume and open interest: Kalshi returns dollar amounts
        volume_24h = float(raw.get("volume_24h", 0) or 0)
        open_interest = float(raw.get("open_interest", 0) or 0)

        close_time = raw.get("close_time") or raw.get("expiration_time") or ""

        return MarketInfo(
            ticker=ticker,
            title=title,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            mid_price=mid_price,
            spread=spread,
            volume_24h=volume_24h,
            open_interest=open_interest,
            close_time=close_time,
            status=status,
        )
    except Exception as exc:
        logger.debug("_parse_market error: %s | ticker=%s", exc, raw.get("ticker"))
        return None


def _days_to_close(close_time: str) -> float:
    """Return days until close_time, or 0 if unparseable."""
    if not close_time:
        return 0.0
    try:
        # Handle "2024-11-05T00:00:00Z" format
        dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        delta = dt - datetime.now(tz=timezone.utc)
        return max(delta.total_seconds() / 86400, 0.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def passes_filter(market: MarketInfo, filt: MarketFilter) -> tuple[bool, str]:
    """Return (True, "") if market passes all filters, else (False, reason)."""

    if market.status not in filt.allowed_statuses:
        return False, f"status={market.status}"

    if not (filt.min_mid <= market.mid_price <= filt.max_mid):
        return False, f"mid {market.mid_price:.3f} outside [{filt.min_mid}, {filt.max_mid}]"

    if market.spread < filt.min_spread:
        return False, f"spread {market.spread:.4f} < min {filt.min_spread}"

    if market.open_interest > filt.max_open_interest:
        return False, f"OI ${market.open_interest:.0f} > max ${filt.max_open_interest:.0f}"

    days = _days_to_close(market.close_time)
    if days < filt.min_days_to_expiry:
        return False, f"only {days:.1f} days to close"

    if filt.max_days_to_expiry > 0 and days > filt.max_days_to_expiry:
        return False, f"{days:.1f} days to close > max {filt.max_days_to_expiry}"

    return True, ""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def market_attractiveness(market: MarketInfo, config: BotConfig) -> float:
    """
    Composite score for ranking markets. Higher = more attractive.

    Components:
      a) Spread headroom  – wider spread = more room to profit
      b) Balance          – proximity of mid to 0.50 (lower directional risk)
      c) Volume           – more volume = better fill probability
    """
    # a) Spread headroom (normalised, capped at 1)
    spread_score = min(market.spread / max(config.market_filter.min_spread, 1e-6), 3.0) / 3.0

    # b) Balance around 0.50
    balance = 1.0 - abs(market.mid_price - 0.50) * 4  # 1 at 0.50, 0 at 0.25/0.75

    # c) Volume (log-normalised, soft cap at $1M)
    import math
    vol_score = math.log1p(market.volume_24h) / math.log1p(1_000_000)
    vol_score = min(vol_score, 1.0)

    return spread_score * 0.5 + balance * 0.3 + vol_score * 0.2


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fee_break_even_spread(config: BotConfig) -> float:
    """
    Minimum observed bid-ask spread for expected net P&L > 0 after Kalshi fees.

    Derived from: gross_captured > fee_on_both_fills
      2 * depth > fee_rate * (1 - 2 * depth)
      where depth = spread * order_depth_fraction

    Solving for spread:
      s_min = fee_rate / (2 * depth_fraction * (1 + fee_rate))

    Special case: if the default_v floor already makes us fee-positive
    (because default_v * depth_fraction yields enough gross), return 0.
    """
    d = config.scoring.order_depth_fraction
    r = config.risk.fee_rate
    if r <= 0 or d <= 0:
        return 0.0
    # Check if the volatility floor alone is sufficient
    default_depth = config.scoring.default_v * d
    if 2 * default_depth > r * (1 - 2 * default_depth):
        return 0.0
    return r / (2 * d * (1 + r))


def select_markets(
    client: KalshiClient,
    config: BotConfig,
    max_markets: int = 10,
) -> list[MarketInfo]:
    """
    Fetch open markets, apply filters, rank, return top candidates.

    Two-stage filter:
      1. MarketFilter criteria (mid range, spread, OI, expiry)
      2. Fee-profitability gate: spread must cover Kalshi trading fees
    """
    logger.info("Fetching open Kalshi markets…")
    raw_markets = client.get_active_markets(limit=200)
    logger.info("  → %d markets fetched", len(raw_markets))

    # Compute minimum spread needed to turn a profit after fees
    fee_min = fee_break_even_spread(config)
    effective_min_spread = max(config.market_filter.min_spread, fee_min)
    if fee_min > config.market_filter.min_spread:
        logger.info(
            "Fee gate: min spread raised %.4f → %.4f "
            "(fee_rate=%.0f%% depth_frac=%.2f)",
            config.market_filter.min_spread, fee_min,
            config.risk.fee_rate * 100, config.scoring.order_depth_fraction,
        )

    candidates: list[tuple[float, MarketInfo]] = []

    for raw in raw_markets:
        market = _parse_market(raw)
        if market is None:
            continue

        ok, reason = passes_filter(market, config.market_filter)
        if not ok:
            logger.debug("  Skip %s: %s", market.ticker, reason)
            continue

        # Fee-profitability gate (spread must be wide enough to cover fees)
        if market.spread < effective_min_spread:
            logger.debug(
                "  Skip %s: spread %.4f < fee_min %.4f",
                market.ticker, market.spread, effective_min_spread,
            )
            continue

        score = market_attractiveness(market, config)
        candidates.append((score, market))
        logger.debug(
            "  OK  %s | mid=%.3f spread=%.4f score=%.4f",
            market.ticker, market.mid_price, market.spread, score,
        )

    candidates.sort(key=lambda t: t[0], reverse=True)
    selected = [m for _, m in candidates[:max_markets]]

    logger.info(
        "Selected %d/%d markets after filtering and ranking.",
        len(selected), len(raw_markets),
    )
    return selected


def title_short(title: str, max_len: int = 60) -> str:
    return title[:max_len] + "…" if len(title) > max_len else title
