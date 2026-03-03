"""
Historical price data fetcher for Polymarket backtesting.

Data source: CLOB prices-history endpoint
  GET https://clob.polymarket.com/prices-history?market={token_id}&interval=max

Returns minute-by-minute {t: unix_ts, p: price} ticks for the full market lifetime.

A local JSON cache avoids re-fetching on repeated runs:
  ~/.cache/polymarket_backtest/{token_id}.json
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
_UA        = {"User-Agent": "Mozilla/5.0 polymarket-backtest/1.0"}
_CACHE_DIR = os.path.expanduser("~/.cache/polymarket_backtest")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PriceTick:
    timestamp: int    # unix seconds
    price: float      # YES token price (probability 0-1)


@dataclass
class MarketHistory:
    condition_id:  str
    question:      str
    yes_token_id:  str
    no_token_id:   str
    start_date:    str   # ISO date string
    end_date:      str
    ticks:         list[PriceTick]
    resolved_yes:  bool

    @property
    def span_hours(self) -> float:
        if not self.ticks:
            return 0.0
        return (self.ticks[-1].timestamp - self.ticks[0].timestamp) / 3600

    @property
    def price_series(self) -> list[float]:
        return [t.price for t in self.ticks]

    def time_near_50(self, lo: float = 0.35, hi: float = 0.65) -> float:
        """Fraction of ticks where price was within [lo, hi]."""
        if not self.ticks:
            return 0.0
        near = sum(1 for t in self.ticks if lo <= t.price <= hi)
        return near / len(self.ticks)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 10, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.debug("GET %s failed: %s", url[:80], exc)
    return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(token_id: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{token_id}.json")


def _load_cache(token_id: str) -> Optional[list[dict]]:
    path = _cache_path(token_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(token_id: str, history: list[dict]) -> None:
    try:
        with open(_cache_path(token_id), "w") as f:
            json.dump(history, f)
    except Exception as exc:
        logger.debug("Cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_price_history(
    token_id: str,
    use_cache: bool = True,
) -> list[PriceTick]:
    """
    Return the full price history for a YES token as PriceTick list.

    Results are cached locally to avoid repeated API calls.
    """
    if use_cache:
        cached = _load_cache(token_id)
        if cached is not None:
            logger.debug("Cache hit for %s", token_id[:20])
            return [PriceTick(t["t"], float(t["p"])) for t in cached]

    url = f"{CLOB_HOST}/prices-history?market={token_id}&interval=max"
    data = _get(url)
    if not data:
        logger.warning("No price history for token %s", token_id[:20])
        return []

    raw = data.get("history", [])
    if use_cache:
        _save_cache(token_id, raw)

    return [PriceTick(int(t["t"]), float(t["p"])) for t in raw]


def fetch_market_list(
    limit: int = 200,
    closed: bool = True,
    active: bool = False,
    order: str = "closedTime",
    ascending: bool = False,
) -> list[dict]:
    """Fetch raw market dicts from Gamma API."""
    params = (
        f"active={'true' if active else 'false'}"
        f"&closed={'true' if closed else 'false'}"
        f"&limit={limit}"
        f"&order={order}"
        f"&ascending={'true' if ascending else 'false'}"
    )
    url = f"{GAMMA_HOST}/markets?{params}"
    data = _get(url)
    return data or []


def build_market_history(
    raw_market: dict,
    use_cache: bool = True,
    min_ticks: int = 50,
) -> Optional[MarketHistory]:
    """
    Build a MarketHistory from a Gamma-API market dict.
    Returns None if price history is unavailable or too short.
    """
    clob = raw_market.get("clobTokenIds", "")
    if not clob:
        return None
    try:
        toks = json.loads(clob)
    except Exception:
        return None
    if len(toks) < 2:
        return None

    yes_tok = toks[0]
    no_tok  = toks[1]

    ticks = fetch_price_history(yes_tok, use_cache=use_cache)
    if len(ticks) < min_ticks:
        return None

    final_price = ticks[-1].price
    return MarketHistory(
        condition_id=raw_market.get("conditionId", ""),
        question=raw_market.get("question", ""),
        yes_token_id=yes_tok,
        no_token_id=no_tok,
        start_date=raw_market.get("startDateIso", "")[:10],
        end_date=raw_market.get("endDateIso", "")[:10],
        ticks=ticks,
        resolved_yes=(final_price > 0.5),
    )


def discover_backtest_markets(
    n: int = 10,
    min_ticks: int = 100,
    min_near50_fraction: float = 0.05,
    use_cache: bool = True,
) -> list[MarketHistory]:
    """
    Fetch recently closed markets and return those suitable for backtesting.

    Selection:
      - Has price history with >= min_ticks points
      - At least min_near50_fraction of time spent near 50/50

    Parameters
    ----------
    n                    : target number of markets to return
    min_ticks            : minimum data points required
    min_near50_fraction  : minimum fraction of time near 0.35-0.65
    use_cache            : use local disk cache for price history

    Returns
    -------
    list[MarketHistory] sorted by span_hours descending
    """
    logger.info("Fetching market list from Gamma API…")
    raw_list = fetch_market_list(limit=300, closed=True)
    logger.info("  %d markets returned", len(raw_list))

    results: list[MarketHistory] = []
    scanned = 0

    for raw in raw_list:
        if len(results) >= n:
            break
        scanned += 1
        mh = build_market_history(raw, use_cache=use_cache, min_ticks=min_ticks)
        if mh is None:
            continue
        if mh.time_near_50() < min_near50_fraction:
            logger.debug("Skip %s – near50=%.1f%%", mh.question[:40], mh.time_near_50() * 100)
            continue
        results.append(mh)
        logger.info(
            "  + %-55s  ticks=%4d  h=%5.1f  near50=%.0f%%",
            mh.question[:55], len(mh.ticks), mh.span_hours, mh.time_near_50() * 100,
        )

    results.sort(key=lambda m: m.span_hours, reverse=True)
    logger.info("Discovered %d backtest markets (scanned %d)", len(results), scanned)
    return results
