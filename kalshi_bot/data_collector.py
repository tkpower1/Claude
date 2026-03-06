"""
Market data collector for the Kalshi market-making bot.

Polls the Kalshi REST API on a fixed interval and stores order-book
snapshots in a local SQLite database for later replay / backtesting.

Usage (from repo root):
    python -m kalshi_bot.data_collector --db market_data.db
    python -m kalshi_bot.data_collector --db market_data.db --interval 60
    python -m kalshi_bot.data_collector --db market_data.db --stats
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timezone

from .client import KalshiClient
from .config import BotConfig
from .market_selector import _parse_market

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    ticker        TEXT    NOT NULL,
    yes_bid       REAL,
    yes_ask       REAL,
    volume_24h    REAL,
    open_interest REAL,
    mid           REAL,
    spread        REAL,
    status        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts        ON market_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_ticker    ON market_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_ticker_ts ON market_snapshots(ticker, ts);
"""


class DataCollector:
    """Poll the Kalshi API and persist market snapshots to SQLite."""

    def __init__(self, db_path: str, config: BotConfig) -> None:
        self.db_path = db_path
        self.client = KalshiClient(config)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
        logger.info("Database ready: %s", self.db_path)

    def collect_once(self) -> int:
        """Single poll: fetch all open markets and store snapshots. Returns row count."""
        try:
            raw_markets = self.client.get_active_markets()
        except Exception as exc:
            logger.error("API fetch failed: %s", exc)
            return 0

        ts = int(time.time())
        rows = []
        for raw in raw_markets:
            m = _parse_market(raw)
            if m is None:
                continue
            rows.append((
                ts,
                m.ticker,
                m.yes_bid,
                m.yes_ask,
                m.volume_24h,
                m.open_interest,
                m.mid_price,
                m.spread,
                m.status,
            ))
        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """INSERT INTO market_snapshots
                   (ts, ticker, yes_bid, yes_ask, volume_24h, open_interest, mid, spread, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def run(self, interval: int = 60) -> None:
        """Run collection loop indefinitely (Ctrl-C to stop)."""
        logger.info("Collector started — interval=%ds  db=%s", interval, self.db_path)
        while True:
            n = self.collect_once()
            logger.info(
                "[%s] Stored %d snapshots",
                datetime.now(tz=timezone.utc).strftime("%H:%M:%S"),
                n,
            )
            time.sleep(interval)


def _print_stats(db_path: str) -> None:
    """Print summary statistics about the collected dataset."""
    with sqlite3.connect(db_path) as conn:
        total,  = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()
        tickers,= conn.execute("SELECT COUNT(DISTINCT ticker) FROM market_snapshots").fetchone()
        row = conn.execute("SELECT MIN(ts), MAX(ts) FROM market_snapshots").fetchone()
        first, last = row if row else (None, None)

    span_h = (last - first) / 3600.0 if first and last else 0.0
    fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n  Database     : {db_path}")
    print(f"  Rows         : {total:,}")
    print(f"  Tickers      : {tickers:,}")
    print(f"  Span         : {span_h:.1f} hours")
    print(f"  First sample : {fmt(first) if first else 'n/a'}")
    print(f"  Last sample  : {fmt(last)  if last  else 'n/a'}\n")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Kalshi market data collector")
    parser.add_argument("--db",       default="market_data.db", help="SQLite database path")
    parser.add_argument("--interval", type=int, default=60,    help="Poll interval (seconds)")
    parser.add_argument("--stats",    action="store_true",      help="Print DB stats and exit")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.stats:
        _print_stats(args.db)
    else:
        cfg = BotConfig()
        DataCollector(args.db, cfg).run(interval=args.interval)
