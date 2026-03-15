"""
Thin wrapper around the Kalshi Trade API v2 REST endpoints.

Authentication: RSA-PSS signed requests.
  Each request requires headers:
    KALSHI-ACCESS-KEY       – API key ID
    KALSHI-ACCESS-TIMESTAMP – milliseconds since epoch (string)
    KALSHI-ACCESS-SIGNATURE – base64(RSA-PSS-SHA256(timestamp + method + path))

Docs: https://trading-api.readme.kalshi.com/docs

Price convention (internal):
  All prices stored as probability floats (0.0 – 1.0).
  Kalshi's API uses integer cents (0 – 100).
  Conversion: api_cents = round(prob * 100)

Contract count:
  Kalshi trades in integer contract counts, not dollar notional.
  Each contract pays $1.00 at resolution.
  size_in_dollars ≈ price * count  (for the side purchased)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from .config import BotConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class MarketInfo:
    ticker: str
    title: str
    yes_bid: float        # probability 0-1
    yes_ask: float        # probability 0-1
    no_bid: float         # probability 0-1  (= 1 - yes_ask)
    no_ask: float         # probability 0-1  (= 1 - yes_bid)
    mid_price: float      # (yes_bid + yes_ask) / 2
    spread: float         # yes_ask - yes_bid
    volume_24h: float     # USD
    open_interest: float  # USD
    close_time: str       # ISO-8601
    status: str           # "open" | "closed" | "settled"


@dataclass
class OrderBook:
    ticker: str
    yes_bids: list[tuple[float, int]]   # [(price_prob, count), ...] descending
    yes_asks: list[tuple[float, int]]   # [(price_prob, count), ...] ascending
    mid: float
    spread: float


@dataclass
class Order:
    order_id: str
    ticker: str
    side: str             # "yes" | "no"
    action: str           # "buy" | "sell"
    price: float          # probability 0-1
    count: int            # number of contracts
    status: str           # "resting" | "filled" | "canceled"
    filled_count: int = 0
    created_time: str = ""


# ---------------------------------------------------------------------------
# HTTP session with retry
# ---------------------------------------------------------------------------

def _make_session(retries: int = 4, backoff: float = 1.0) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "DELETE"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# RSA-PSS signing
# ---------------------------------------------------------------------------

def _sign_request(private_key, method: str, path: str) -> tuple[str, str]:
    """
    Produce (timestamp_ms_str, base64_signature) for a Kalshi API request.

    Signing payload: timestamp_ms + method.upper() + path
    (path = everything before '?', including leading slash)
    """
    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + method.upper() + path).encode("utf-8")

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    signature = private_key.sign(
        msg,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return ts_ms, base64.b64encode(signature).decode("utf-8")


# ---------------------------------------------------------------------------
# Kalshi client
# ---------------------------------------------------------------------------

class KalshiClient:
    """
    Wraps Kalshi Trade API v2 calls.

    All prices are stored/returned as probability floats (0.0–1.0).
    Conversion to/from Kalshi's integer cent format happens here.
    """

    def __init__(self, config: BotConfig) -> None:
        self.cfg = config
        self._session = _make_session()
        self._base = config.api_base
        self._private_key = None

        if not config.dry_run:
            try:
                self._private_key = config.load_private_key()
                logger.info("Kalshi RSA key loaded (key_id=%s).", config.api_key_id)
            except Exception as exc:
                logger.error("Failed to load private key: %s", exc)
                raise

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, method: str, path: str) -> dict:
        """Return the three Kalshi auth headers for a signed request."""
        if self._private_key is None:
            return {}
        ts, sig = _sign_request(self._private_key, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.cfg.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    def _full_path(self, path: str) -> str:
        """Return the full API path used in signing (e.g. /trade-api/v2/portfolio/balance)."""
        from urllib.parse import urlparse
        parsed = urlparse(self._base)
        return parsed.path + path

    def _get(self, path: str, params: dict | None = None, auth: bool = True) -> Any:
        url = self._base + path
        headers = self._auth_headers("GET", self._full_path(path)) if auth else {}
        resp = self._session.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        url = self._base + path
        headers = self._auth_headers("POST", self._full_path(path))
        resp = self._session.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> Any:
        url = self._base + path
        headers = self._auth_headers("DELETE", self._full_path(path))
        resp = self._session.delete(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Price conversions
    # ------------------------------------------------------------------

    @staticmethod
    def _to_cents(prob: float) -> int:
        """Convert probability (0-1) to Kalshi integer cents (0-100)."""
        return max(1, min(99, round(prob * 100)))

    @staticmethod
    def _to_prob(cents: int | float) -> float:
        """Convert Kalshi cents to probability float."""
        return float(cents) / 100.0

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_active_markets(self, limit: int = 100) -> list[dict]:
        """Return raw market dicts from the Kalshi markets endpoint."""
        params = {"status": "open", "limit": min(limit, 200)}
        data = self._get("/markets", params=params, auth=False)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        data = self._get(f"/markets/{ticker}", auth=False)
        return data.get("market", data)

    def get_order_book(self, ticker: str) -> OrderBook:
        """
        Fetch live order book for a market.

        Kalshi only returns YES bids. YES asks are derived from NO bids:
          yes_ask_price = 1 - no_bid_price
        """
        data = self._get(f"/markets/{ticker}/orderbook", auth=False)
        book = data.get("orderbook", data)

        yes_bids_raw = book.get("yes", [])   # [[price_cents, count], ...]
        no_bids_raw = book.get("no", [])

        # YES bids: descending
        yes_bids = [
            (self._to_prob(row[0]), int(row[1]))
            for row in yes_bids_raw
        ]
        # YES asks derived from NO bids (ascending = lowest ask first)
        yes_asks = sorted(
            [(self._to_prob(100 - row[0]), int(row[1])) for row in no_bids_raw],
            key=lambda x: x[0],
        )

        best_bid = yes_bids[0][0] if yes_bids else 0.0
        best_ask = yes_asks[0][0] if yes_asks else 1.0
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        return OrderBook(
            ticker=ticker,
            yes_bids=yes_bids,
            yes_asks=yes_asks,
            mid=mid,
            spread=spread,
        )

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        """Return available USD balance."""
        if self.cfg.dry_run:
            return self.cfg.risk.total_budget

        try:
            data = self._get("/portfolio/balance")
            # Kalshi returns balance in cents
            cents = data.get("balance", 0)
            return float(cents) / 100.0
        except Exception as exc:
            logger.error("get_balance error: %s", exc)
            return 0.0

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        ticker: str,
        side: str,        # "yes" | "no"
        action: str,      # "buy" | "sell"
        price: float,     # probability 0-1
        count: int,       # number of contracts
    ) -> Optional[str]:
        """
        Submit a GTC limit order. Returns order_id on success, None on failure.
        """
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] Would place %s %s %s @ %.4f × %d contracts",
                action.upper(), side.upper(), ticker, price, count,
            )
            return f"dry-{ticker[:12]}-{side}-{action}-{int(time.time())}"

        try:
            body = {
                "ticker": ticker,
                "action": action,
                "side": side,
                "type": "limit",
                "yes_price": self._to_cents(price) if side == "yes" else 100 - self._to_cents(price),
                "count": count,
            }
            resp = self._post("/portfolio/orders", body)
            order = resp.get("order", resp)
            order_id = order.get("order_id", "")
            logger.info(
                "Order placed: %s %s %s @ %.4f ×%d → id=%s",
                action.upper(), side.upper(), ticker, price, count, order_id,
            )
            return order_id
        except Exception as exc:
            logger.error("place_limit_order error: %s", exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a single open order. Returns True on success."""
        if self.cfg.dry_run:
            logger.info("[DRY-RUN] Would cancel order %s", order_id)
            return True

        try:
            self._delete(f"/portfolio/orders/{order_id}")
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("cancel_order(%s) error: %s", order_id, exc)
            return False

    def get_open_orders(self) -> list[Order]:
        """Return list of currently resting orders for this account."""
        if self.cfg.dry_run:
            return []

        try:
            data = self._get("/portfolio/orders", params={"status": "resting"})
            raw_orders = data.get("orders", [])
            orders = []
            for o in raw_orders:
                # yes_price is always present; no_price = 100 - yes_price
                yes_price_cents = o.get("yes_price", 50)
                side = o.get("side", "yes").lower()
                if side == "yes":
                    price_prob = self._to_prob(yes_price_cents)
                else:
                    price_prob = self._to_prob(100 - yes_price_cents)

                orders.append(Order(
                    order_id=o.get("order_id", ""),
                    ticker=o.get("ticker", ""),
                    side=side,
                    action=o.get("action", "buy").lower(),
                    price=price_prob,
                    count=int(o.get("count", 0)),
                    status=o.get("status", "resting").lower(),
                    filled_count=int(o.get("filled_count", 0)),
                    created_time=o.get("created_time", ""),
                ))
            return orders
        except Exception as exc:
            logger.error("get_open_orders error: %s", exc)
            return []

    def get_fills(self, ticker: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Fetch recent fills (trades)."""
        if self.cfg.dry_run:
            return []

        try:
            params: dict = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            data = self._get("/portfolio/fills", params=params)
            return data.get("fills", [])
        except Exception as exc:
            logger.error("get_fills error: %s", exc)
            return []

    def place_market_sell_order(
        self,
        ticker: str,
        side: str,   # "yes" | "no"
        count: int,
    ) -> Optional[str]:
        """
        Submit a market SELL order to exit a held position immediately.

        Returns order_id on success, None on failure.
        """
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] Would market-sell %d %s contracts on %s",
                count, side.upper(), ticker,
            )
            return f"dry-sell-{ticker[:12]}-{side}-{int(time.time())}"

        try:
            body = {
                "ticker": ticker,
                "action": "sell",
                "side": side,
                "type": "market",
                "count": count,
            }
            resp = self._post("/portfolio/orders", body)
            order = resp.get("order", resp)
            order_id = order.get("order_id", "")
            logger.info(
                "Market sell: %s %s ×%d → id=%s",
                side.upper(), ticker, count, order_id,
            )
            return order_id
        except Exception as exc:
            logger.error("place_market_sell_order error (%s %s): %s", ticker, side, exc)
            return None

    def get_order_status(self, order_id: str) -> Optional[Order]:
        """
        Fetch a single order by ID to determine whether it was filled or cancelled.

        Returns None on error (caller should treat as unknown / re-check later).
        """
        if self.cfg.dry_run:
            return None

        try:
            data = self._get(f"/portfolio/orders/{order_id}")
            o = data.get("order", data)
            yes_price_cents = o.get("yes_price", 50)
            side = o.get("side", "yes").lower()
            price_prob = (
                self._to_prob(yes_price_cents)
                if side == "yes"
                else self._to_prob(100 - yes_price_cents)
            )
            return Order(
                order_id=o.get("order_id", order_id),
                ticker=o.get("ticker", ""),
                side=side,
                action=o.get("action", "buy").lower(),
                price=price_prob,
                count=int(o.get("count", 0)),
                status=o.get("status", "").lower(),
                filled_count=int(o.get("filled_count", 0)),
                created_time=o.get("created_time", ""),
            )
        except Exception as exc:
            logger.error("get_order_status(%s) error: %s", order_id, exc)
            return None

    def get_portfolio_positions(self) -> list[dict]:
        """
        Return all current contract positions held in the portfolio.

        Each entry is a raw Kalshi market_position dict with keys including:
          ticker, position (net YES contracts), total_cost (cents),
          market_exposure (cents), realized_pnl (cents), unrealized_pnl (cents).
        """
        if self.cfg.dry_run:
            return []

        try:
            data = self._get("/portfolio/positions")
            return data.get("market_positions", [])
        except Exception as exc:
            logger.error("get_portfolio_positions error: %s", exc)
            return []
