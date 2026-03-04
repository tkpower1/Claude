"""
SQLite-backed persistence for live bot position state.

Writes every state transition to disk so the bot can resume without
losing track of open orders after a restart or crash.

Usage:
    store = StateStore("bot_state.db")
    positions = store.load()          # on startup
    store.upsert(pos)                 # after every state change
    store.delete(ticker)              # optional cleanup
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .order_manager import MarketPosition, PositionState

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    ticker          TEXT PRIMARY KEY,
    title           TEXT    NOT NULL DEFAULT '',
    yes_price       REAL    NOT NULL DEFAULT 0,
    no_price        REAL    NOT NULL DEFAULT 0,
    contracts       INTEGER NOT NULL DEFAULT 0,
    yes_order_id    TEXT,
    no_order_id     TEXT,
    hedge_order_id  TEXT,
    hedge_price     REAL    NOT NULL DEFAULT 0,
    filled_side     TEXT    NOT NULL DEFAULT '',
    state           TEXT    NOT NULL,
    realised_pnl    REAL    NOT NULL DEFAULT 0,
    original_mid    REAL    NOT NULL DEFAULT 0,
    last_quote_time REAL    NOT NULL DEFAULT 0
);
"""

# States we bother re-loading on restart (skip IDLE / RESOLVED — nothing to do)
_LIVE_STATES = ("QUOTING", "YES_FILLED", "NO_FILLED", "ONE_SIDE_HEDGED", "BOTH_FILLED")


class StateStore:
    """Persist MarketPosition objects to a local SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
        logger.info("StateStore ready: %s", db_path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, pos: "MarketPosition") -> None:
        """Insert or replace the position row. Call after every state change."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO positions
                       (ticker, title, yes_price, no_price, contracts,
                        yes_order_id, no_order_id, hedge_order_id,
                        hedge_price, filled_side,
                        state, realised_pnl, original_mid, last_quote_time)
                       VALUES (?,?,?,?,?, ?,?,?, ?,?, ?,?,?,?)""",
                    (
                        pos.ticker, pos.title,
                        pos.yes_price, pos.no_price, pos.contracts,
                        pos.yes_order_id, pos.no_order_id, pos.hedge_order_id,
                        pos.hedge_price, pos.filled_side,
                        pos.state.name, pos.realised_pnl,
                        pos.original_mid, pos.last_quote_time,
                    ),
                )
        except Exception as exc:
            logger.error("StateStore.upsert(%s): %s", pos.ticker, exc)

    def delete(self, ticker: str) -> None:
        """Remove a position row (e.g. after confirmed RESOLVED cleanup)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
        except Exception as exc:
            logger.error("StateStore.delete(%s): %s", ticker, exc)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> "dict[str, MarketPosition]":
        """Re-hydrate all live positions from disk. Call once on startup."""
        # Import here to avoid circular import at module level
        from .order_manager import MarketPosition, PositionState

        positions: dict[str, MarketPosition] = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    f"""SELECT ticker, title, yes_price, no_price, contracts,
                               yes_order_id, no_order_id, hedge_order_id,
                               hedge_price, filled_side,
                               state, realised_pnl, original_mid, last_quote_time
                        FROM positions
                        WHERE state IN ({','.join('?'*len(_LIVE_STATES))})""",
                    _LIVE_STATES,
                ).fetchall()
        except Exception as exc:
            logger.error("StateStore.load: %s", exc)
            return {}

        for row in rows:
            (
                ticker, title, yes_price, no_price, contracts,
                yes_order_id, no_order_id, hedge_order_id,
                hedge_price, filled_side,
                state_name, realised_pnl, original_mid, last_quote_time,
            ) = row
            try:
                state = PositionState[state_name]
            except KeyError:
                logger.warning("Unknown state '%s' for %s – skipping.", state_name, ticker)
                continue

            pos = MarketPosition(
                ticker=ticker,
                title=title or "",
                yes_price=yes_price or 0.0,
                no_price=no_price or 0.0,
                contracts=int(contracts or 0),
                yes_order_id=yes_order_id,
                no_order_id=no_order_id,
                hedge_order_id=hedge_order_id,
                hedge_price=hedge_price or 0.0,
                filled_side=filled_side or "",
                state=state,
                realised_pnl=realised_pnl or 0.0,
                original_mid=original_mid or 0.0,
                last_quote_time=last_quote_time or time.time(),
            )
            positions[ticker] = pos
            logger.info("Restored position %s [%s]", ticker, state_name)

        if positions:
            logger.info("StateStore: restored %d live position(s).", len(positions))
        return positions
