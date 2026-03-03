"""
Market selection: filter and rank Polymarket markets for the LP/hedge strategy.

Selection criteria (from paper §5):
  1. Mid price near 50/50 (widest reward window, most balanced risk)
  2. Minimum bid-ask spread ≥ min_spread (confirms there is a gap to fill)
  3. Low open interest (less competition for reward share)
  4. Sufficient time to expiry (rewards accrue over days)
  5. Positive reward pool / non-zero daily USDC programme
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from .client import ClobClient, MarketInfo, OrderBook
from .config import BotConfig, MarketFilter
from .rewards import order_score, ladder_total_score, LadderSpec, estimate_reward_share

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing raw API responses into MarketInfo
# ---------------------------------------------------------------------------

def _parse_market(raw: dict, rewards_raw: dict) -> Optional[MarketInfo]:
    """
    Convert a raw Gamma-API market dict into a MarketInfo dataclass.
    Returns None if essential fields are missing.
    """
    try:
        condition_id = raw.get("conditionId") or raw.get("condition_id", "")
        question = raw.get("question", "Unknown")
        active = raw.get("active", False) and not raw.get("closed", True)

        # Token IDs
        tokens = raw.get("tokens") or raw.get("clob_token_ids") or []
        if len(tokens) < 2:
            return None

        # Polymarket tokens[0] = YES, tokens[1] = NO (by convention)
        # Some responses use dicts, some use plain strings
        if isinstance(tokens[0], dict):
            yes_token_id = tokens[0].get("token_id", "")
            no_token_id = tokens[1].get("token_id", "")
        else:
            yes_token_id = str(tokens[0])
            no_token_id = str(tokens[1])

        # Best bid / ask (YES token)
        best_bid = float(raw.get("bestBid") or raw.get("best_bid") or 0.0)
        best_ask = float(raw.get("bestAsk") or raw.get("best_ask") or 1.0)
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        volume_24h = float(raw.get("volume24hr") or raw.get("volume_24h") or 0.0)
        open_interest = float(raw.get("openInterest") or raw.get("open_interest") or 0.0)
        end_date = raw.get("endDate") or raw.get("end_date_iso") or ""

        # Reward programme
        reward_rate = 0.0
        max_spread = 0.05     # default v
        multiplier = 1.0

        if rewards_raw:
            reward_rate = float(rewards_raw.get("rewardRate") or
                                rewards_raw.get("reward_rate") or 0.0)
            max_spread = float(rewards_raw.get("maxSpread") or
                               rewards_raw.get("max_spread") or 0.05)
            multiplier = float(rewards_raw.get("multiplier") or 1.0)

        return MarketInfo(
            condition_id=condition_id,
            question=question,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            mid_price=mid_price,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            volume_24h=volume_24h,
            open_interest=open_interest,
            end_date_iso=end_date,
            active=active,
            reward_rate=reward_rate,
            max_spread=max_spread,
            multiplier=multiplier,
        )
    except Exception as exc:
        logger.debug("_parse_market error: %s | raw=%s", exc, raw.get("conditionId"))
        return None


def _days_to_expiry(end_date_iso: str) -> float:
    """Return number of days until resolution, or 0 if unparseable."""
    if not end_date_iso:
        return 0.0
    try:
        # Handle both "2024-11-05T00:00:00Z" and "2024-11-05" formats
        fmt = "%Y-%m-%dT%H:%M:%SZ" if "T" in end_date_iso else "%Y-%m-%d"
        dt = datetime.strptime(end_date_iso[:19].rstrip("Z"), fmt.rstrip("Z"))
        dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(tz=timezone.utc)
        return max(delta.total_seconds() / 86400, 0.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Filter pass
# ---------------------------------------------------------------------------

def passes_filter(market: MarketInfo, filt: MarketFilter) -> tuple[bool, str]:
    """
    Return (True, "") if market passes all selection criteria, else (False, reason).
    """
    if not market.active:
        return False, "not active"

    if not (filt.min_mid <= market.mid_price <= filt.max_mid):
        return False, f"mid {market.mid_price:.3f} outside [{filt.min_mid}, {filt.max_mid}]"

    if market.spread < filt.min_spread:
        return False, f"spread {market.spread:.4f} < min {filt.min_spread}"

    if market.open_interest > filt.max_open_interest:
        return False, f"OI {market.open_interest:.0f} > max {filt.max_open_interest:.0f}"

    days = _days_to_expiry(market.end_date_iso)
    if days < filt.min_days_to_expiry:
        return False, f"only {days:.1f} days to expiry"

    return True, ""


# ---------------------------------------------------------------------------
# Scoring / ranking
# ---------------------------------------------------------------------------

def market_attractiveness(market: MarketInfo, config: BotConfig) -> float:
    """
    Composite score for ranking markets. Higher = more attractive.

    Components:
      a) Expected daily reward income per dollar of liquidity provided
      b) Proximity of mid to 0.50 (balanced risk)
      c) Spread relative to reward window (how much room we have)
    """
    sc = config.scoring
    risk = config.risk

    # a) Reward income per dollar
    # Approximate: if we post 1 order level at the ideal depth our score is S*
    depth = market.max_spread * sc.order_depth_fraction
    s_score = order_score(depth, market.max_spread, market.multiplier)
    # We estimate we capture a small share (5 %) of total competing score
    reward_income = estimate_reward_share(
        our_score=s_score,
        total_competing_score=s_score * 20,   # optimistic competition estimate
        daily_pool_usdc=market.reward_rate,
    )
    reward_per_dollar = reward_income / max(risk.total_budget * 0.01, 1.0)

    # b) Proximity to 0.50
    balance = 1.0 - abs(market.mid_price - 0.50) * 4   # 1.0 at mid=0.50, 0 at 0.25/0.75

    # c) Spread headroom vs reward window
    headroom = market.spread / max(market.max_spread, 1e-6)

    return reward_per_dollar * 0.5 + balance * 0.3 + headroom * 0.2


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def select_markets(
    client: ClobClient,
    config: BotConfig,
    max_markets: int = 10,
) -> list[MarketInfo]:
    """
    Fetch all active markets, apply filters, rank, and return the top candidates.

    Parameters
    ----------
    client     : authenticated ClobClient
    config     : BotConfig
    max_markets: maximum number of markets to return

    Returns
    -------
    list[MarketInfo] sorted by attractiveness descending
    """
    logger.info("Fetching active markets…")
    raw_markets = client.get_active_markets(limit=200)
    logger.info("  → %d markets fetched", len(raw_markets))

    candidates: list[tuple[float, MarketInfo]] = []

    for raw in raw_markets:
        condition_id = raw.get("conditionId") or raw.get("condition_id", "")
        if not condition_id:
            continue

        # Fetch reward info (can be empty)
        rewards_raw = client.get_rewards_info(condition_id)

        market = _parse_market(raw, rewards_raw)
        if market is None:
            continue

        ok, reason = passes_filter(market, config.market_filter)
        if not ok:
            logger.debug("  Skip %s: %s", question_short(market.question), reason)
            continue

        score = market_attractiveness(market, config)
        candidates.append((score, market))
        logger.debug(
            "  OK  %s | mid=%.3f spread=%.4f score=%.4f",
            question_short(market.question), market.mid_price, market.spread, score,
        )

    candidates.sort(key=lambda t: t[0], reverse=True)
    selected = [m for _, m in candidates[:max_markets]]

    logger.info(
        "Selected %d/%d markets after filtering and ranking.",
        len(selected), len(raw_markets),
    )
    return selected


def question_short(question: str, max_len: int = 60) -> str:
    return question[:max_len] + "…" if len(question) > max_len else question
