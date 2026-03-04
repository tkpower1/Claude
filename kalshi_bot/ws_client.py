"""
WebSocket client for real-time Kalshi order fill notifications.

Replaces the 60-second REST poll for fill detection with a persistent
WebSocket connection that delivers fill events within milliseconds.

Kalshi WS API v2:
  Endpoint   : wss://trading-api.kalshi.com/trade-api/ws/v2
  Demo       : wss://demo-api.kalshi.co/trade-api/ws/v2
  Auth       : Same RSA-PSS headers as the REST API (GET /trade-api/ws/v2)
  Subscribe  : {"id": 1, "cmd": "subscribe", "params": {"channels": ["fill"]}}
  Fill event : {"type": "fill", "msg": {"market_ticker": "...", "order_id": "...",
                                         "side": "yes"|"no", "count": N,
                                         "yes_price": 48, "no_price": 52}}

Architecture:
  - Runs in a background daemon thread (non-blocking for the main bot loop)
  - Auto-reconnects with exponential backoff (1s → 2s → 4s → … → 60s)
  - On each fill event, calls the supplied on_fill callback
  - OrderManager registers a callback and processes fills immediately
    instead of waiting for the next 60-second REST poll cycle

Usage:
    ws = KalshiWebSocket(config, on_fill=order_manager.handle_ws_fill)
    ws.start()
    ...
    ws.stop()

Dependency: websocket-client  (pip install websocket-client)
  Listed in requirements.txt as websocket-client>=1.6.0
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Callable, Optional

from .config import BotConfig

logger = logging.getLogger(__name__)

# Type alias: callback(ticker, order_id, side, count)
FillCallback = Callable[[str, str, str, int], None]

_WS_PATH = "/trade-api/ws/v2"  # used for signing and building URL


class KalshiWebSocket:
    """
    Persistent WebSocket connection to the Kalshi real-time feed.

    Provides fill notifications to the OrderManager without polling.
    """

    def __init__(self, config: BotConfig, on_fill: FillCallback) -> None:
        self.cfg = config
        self._on_fill = on_fill
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the WebSocket listener in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="kalshi-ws", daemon=True
        )
        self._thread.start()
        logger.info("WebSocket listener started.")

    def stop(self) -> None:
        """Shut down the WebSocket listener cleanly."""
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        logger.info("WebSocket listener stopped.")

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # Reconnect loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                self._connect()
                backoff = 1.0  # reset on successful connection
            except Exception as exc:
                logger.warning("WebSocket connection error: %s", exc)
            finally:
                self._connected.clear()

            if self._running:
                logger.info("WebSocket reconnecting in %.0fs…", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _connect(self) -> None:
        try:
            import websocket  # websocket-client
        except ImportError:
            logger.error(
                "websocket-client not installed. "
                "Run: pip install 'websocket-client>=1.6.0'"
            )
            self._running = False
            return

        ws_url = self._ws_url()
        headers = self._auth_headers()

        ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws = ws
        ws.run_forever(ping_interval=30, ping_timeout=10)

    # ------------------------------------------------------------------
    # WebSocket event handlers
    # ------------------------------------------------------------------

    def _on_open(self, ws) -> None:
        self._connected.set()
        logger.info("WebSocket connected to %s", self._ws_url())
        # Subscribe to fill events for all of our orders
        sub = json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["fill"]},
        })
        ws.send(sub)
        logger.debug("Subscribed to fill channel.")

    def _on_message(self, ws, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("WS non-JSON message: %s", raw[:120])
            return

        msg_type = data.get("type", "")

        if msg_type == "fill":
            self._handle_fill(data.get("msg", {}))
        elif msg_type == "subscribed":
            logger.info("WS subscription confirmed: channels=%s", data.get("msg"))
        elif msg_type == "error":
            logger.warning("WS error message: %s", data.get("msg"))
        else:
            logger.debug("WS message type=%s (ignored)", msg_type)

    def _on_error(self, ws, error) -> None:
        logger.warning("WebSocket error: %s", error)

    def _on_close(self, ws, code, reason) -> None:
        self._connected.clear()
        logger.info("WebSocket closed (code=%s reason=%s).", code, reason)

    # ------------------------------------------------------------------
    # Fill event processing
    # ------------------------------------------------------------------

    def _handle_fill(self, msg: dict) -> None:
        """
        Parse a fill event and invoke the on_fill callback.

        Kalshi fill event fields (v2):
          market_ticker  : str
          order_id       : str
          side           : "yes" | "no"
          count          : int  (contracts filled)
          yes_price      : int  (cents)
          no_price       : int  (cents)
          action         : "buy" | "sell"
          is_taker       : bool
        """
        ticker   = msg.get("market_ticker", "")
        order_id = msg.get("order_id", "")
        side     = msg.get("side", "")
        count    = int(msg.get("count", 0))

        if not ticker or not order_id or count == 0:
            logger.debug("Incomplete fill event: %s", msg)
            return

        logger.info(
            "WS fill: %s | order=%s side=%s count=%d",
            ticker, order_id, side, count,
        )
        try:
            self._on_fill(ticker, order_id, side, count)
        except Exception as exc:
            logger.error("on_fill callback error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        # Derive WS URL from the REST base URL
        base = self.cfg.api_base  # "https://trading-api.kalshi.com/trade-api/v2"
        # Strip /v2 suffix and replace https with wss
        root = base.rsplit("/trade-api/v2", 1)[0]
        return root.replace("https://", "wss://").replace("http://", "ws://") + _WS_PATH

    def _auth_headers(self) -> list[str]:
        """
        Build RSA-PSS auth headers formatted as websocket-client header list.

        websocket-client accepts headers as ["Key: Value", ...].
        """
        if self.cfg.dry_run:
            return []

        try:
            private_key = self.cfg.load_private_key()
        except Exception as exc:
            logger.error("Failed to load private key for WS auth: %s", exc)
            return []

        ts_ms = str(int(time.time() * 1000))
        msg = (ts_ms + "GET" + _WS_PATH).encode("utf-8")

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
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        return [
            f"KALSHI-ACCESS-KEY: {self.cfg.api_key_id}",
            f"KALSHI-ACCESS-TIMESTAMP: {ts_ms}",
            f"KALSHI-ACCESS-SIGNATURE: {sig_b64}",
        ]
