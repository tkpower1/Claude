"""
Thin wrapper around the Polymarket CLOB REST API and Gamma market-data API.

Docs: https://docs.polymarket.com/#clob-rest-api
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from .config import BotConfig, CLOB_HOST, GAMMA_HOST

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class MarketInfo:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    mid_price: float          # probability 0-1
    best_bid: float           # YES best bid
    best_ask: float           # YES best ask
    spread: float             # ask - bid
    volume_24h: float         # USDC
    open_interest: float      # USDC
    end_date_iso: str
    active: bool
    # Reward-pool info (may be absent on some markets)
    reward_rate: float = 0.0  # daily USDC rewards per $ of liquidity
    max_spread: float = 0.05  # v in the scoring function
    multiplier: float = 1.0   # b in the scoring function


@dataclass
class OrderBook:
    yes_token_id: str
    bids: list[tuple[float, float]]   # [(price, size), ...] descending
    asks: list[tuple[float, float]]   # [(price, size), ...] ascending
    mid: float
    spread: float


@dataclass
class Order:
    order_id: str
    token_id: str
    side: str                 # "BUY" | "SELL"
    price: float
    size: float
    status: str               # "LIVE" | "FILLED" | "CANCELLED"
    filled_size: float = 0.0
    created_at: float = 0.0   # unix timestamp


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
# CLOB client
# ---------------------------------------------------------------------------

class ClobClient:
    """
    Wraps Polymarket CLOB API calls.

    Authentication uses the L2 API key + HMAC signing provided by the
    py-clob-client library when it is available. Falls back to unsigned
    (read-only) calls for market data in dry-run mode.
    """

    def __init__(self, config: BotConfig) -> None:
        self.cfg = config
        self._session = _make_session()
        self._gamma = _make_session()
        self._clob_client: Any = None  # py_clob_client instance if available

        if not config.dry_run:
            self._init_auth_client()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_auth_client(self) -> None:
        """Instantiate py_clob_client with credentials."""
        try:
            from py_clob_client.client import ClobClient as _Official
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self.cfg.api_key,
                api_secret=self.cfg.api_secret,
                api_passphrase=self.cfg.api_passphrase,
            )
            self._clob_client = _Official(
                host=CLOB_HOST,
                key=self.cfg.private_key,
                chain_id=137,
                creds=creds,
                signature_type=1,  # POLY_GNOSIS_SAFE or EOA; adjust per wallet
                funder=self.cfg.funder or None,
            )
            logger.info("Authenticated CLOB client initialised.")
        except ImportError:
            logger.warning(
                "py-clob-client not installed – falling back to raw HTTP. "
                "Install it with: pip install py-clob-client"
            )
        except Exception as exc:
            logger.error("CLOB client init error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Market data  (Gamma API – no auth required)
    # ------------------------------------------------------------------

    def get_active_markets(self, limit: int = 100) -> list[dict]:
        """Return raw market dicts from Gamma API."""
        url = f"{GAMMA_HOST}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume24hr",
            "ascending": "false",
        }
        resp = self._gamma.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_market(self, condition_id: str) -> dict:
        """Fetch a single market by condition ID."""
        url = f"{GAMMA_HOST}/markets/{condition_id}"
        resp = self._gamma.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch live order book for a token."""
        url = f"{CLOB_HOST}/book"
        resp = self._session.get(url, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 1.0
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        return OrderBook(
            yes_token_id=token_id,
            bids=bids,
            asks=asks,
            mid=mid,
            spread=spread,
        )

    def get_midpoint(self, token_id: str) -> float:
        """Return current midpoint price for a YES token."""
        url = f"{CLOB_HOST}/midpoint"
        resp = self._session.get(url, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        return float(resp.json().get("mid", 0.5))

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        token_id: str,
        side: str,       # "BUY" | "SELL"
        price: float,
        size: float,
    ) -> Optional[str]:
        """
        Submit a GTC limit order. Returns order_id on success, None on failure.

        In dry-run mode only logs the intent.
        """
        if self.cfg.dry_run:
            logger.info(
                "[DRY-RUN] Would place %s %s @ %.4f size=%.2f",
                side, token_id[:12], price, size,
            )
            return f"dry-{token_id[:8]}-{side}-{int(time.time())}"

        if self._clob_client is None:
            logger.error("No authenticated client – cannot place order.")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=OrderType.GTC,
            )
            resp = self._clob_client.create_and_post_order(order_args)
            order_id = resp.get("orderID") or resp.get("order_id")
            logger.info(
                "Order placed: %s %s @ %.4f size=%.2f → id=%s",
                side, token_id[:12], price, size, order_id,
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

        if self._clob_client is None:
            return False

        try:
            self._clob_client.cancel(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("cancel_order error: %s", exc)
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel every open order across all markets."""
        if self.cfg.dry_run:
            logger.info("[DRY-RUN] Would cancel all orders.")
            return True

        if self._clob_client is None:
            return False

        try:
            self._clob_client.cancel_all()
            logger.info("All orders cancelled.")
            return True
        except Exception as exc:
            logger.error("cancel_all_orders error: %s", exc)
            return False

    def get_open_orders(self) -> list[Order]:
        """Return list of currently open orders for this account."""
        if self.cfg.dry_run:
            return []

        if self._clob_client is None:
            return []

        try:
            raw = self._clob_client.get_orders()
            orders = []
            for o in (raw or []):
                orders.append(Order(
                    order_id=o.get("id") or o.get("order_id", ""),
                    token_id=o.get("asset_id") or o.get("token_id", ""),
                    side=o.get("side", "BUY").upper(),
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size") or o.get("size", 0)),
                    status=o.get("status", "LIVE").upper(),
                    filled_size=float(o.get("size_matched") or o.get("filled", 0)),
                    created_at=float(o.get("created_at", time.time())),
                ))
            return orders
        except Exception as exc:
            logger.error("get_open_orders error: %s", exc)
            return []

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Fetch recent fills."""
        if self.cfg.dry_run or self._clob_client is None:
            return []

        try:
            return self._clob_client.get_trades(limit=limit) or []
        except Exception as exc:
            logger.error("get_trade_history error: %s", exc)
            return []

    def get_balance(self) -> float:
        """Return USDC balance in the CLOB account."""
        if self.cfg.dry_run:
            return self.cfg.risk.total_budget

        if self._clob_client is None:
            return 0.0

        try:
            bal = self._clob_client.get_balance()
            return float(bal)
        except Exception as exc:
            logger.error("get_balance error: %s", exc)
            return 0.0

    # ------------------------------------------------------------------
    # Rewards / liquidity-programme data
    # ------------------------------------------------------------------

    def get_rewards_info(self, condition_id: str) -> dict:
        """
        Fetch reward-pool details for a market from the CLOB rewards endpoint.
        Returns empty dict when no programme exists.
        """
        url = f"{CLOB_HOST}/rewards"
        try:
            resp = self._session.get(
                url, params={"condition_id": condition_id}, timeout=10
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("get_rewards_info(%s): %s", condition_id, exc)
            return {}
