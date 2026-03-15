"""
Entry point: python -m kalshi_bot [options]

Required environment variables:
  KALSHI_API_KEY_ID   – API key ID from kalshi.com/account/profile
  KALSHI_PRIVATE_KEY  – RSA private key PEM string or path to .pem file

Optional:
  KALSHI_DEMO=true    – Use demo environment (safe for testing)
  KALSHI_DRY_RUN=true – Simulate orders without submitting

Example (demo dry-run):
  KALSHI_DEMO=true KALSHI_DRY_RUN=true python -m kalshi_bot --budget 1000
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import BotConfig, MarketFilter, RiskParams, ScoringParams
from .bot import KalshiBot


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    for lib in ("urllib3", "requests"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kalshi_bot",
        description="Kalshi Market-Making Bot – spread-capture liquidity strategy",
    )

    # Emergency exit
    p.add_argument("--close-all", action="store_true",
                   help="Cancel all resting orders and market-sell all held positions, then exit")

    # Operational
    p.add_argument("--dry-run", action="store_true",
                   help="Simulate orders without submitting")
    p.add_argument("--demo", action="store_true",
                   help="Use Kalshi demo environment")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--scan-interval", type=int, default=60,
                   help="Seconds between market scans (default: 60)")

    # Budget / risk
    p.add_argument("--budget", type=float, default=None,
                   help="Total USD budget")
    p.add_argument("--max-fill-cost", type=float, default=1.02,
                   help="Max YES+NO combined cost (default: 1.02)")
    p.add_argument("--kelly-mult", type=float, default=0.25,
                   help="Kelly multiplier (default: 0.25)")
    p.add_argument("--order-levels", type=int, default=3,
                   help="Ladder levels per side (default: 3)")
    p.add_argument("--fee-rate", type=float, default=None,
                   help="Kalshi fee rate fraction (default: 0.07); set 0 to disable fee gate")
    p.add_argument("--depth-frac", type=float, default=None,
                   help="Order depth fraction inside spread (default: 0.40); "
                        "higher values lower the fee-gate spread threshold")

    # Market filter
    p.add_argument("--min-mid", type=float, default=0.40)
    p.add_argument("--max-mid", type=float, default=0.60)
    p.add_argument("--min-spread", type=float, default=0.03)
    p.add_argument("--min-days", type=int, default=3)

    # Persistence
    p.add_argument("--state-db", type=str, default=None, metavar="PATH",
                   help="SQLite file for position persistence (enables crash recovery)")

    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    risk = RiskParams(
        max_fill_cost=args.max_fill_cost,
        kelly_multiplier=args.kelly_mult,
        order_levels=args.order_levels,
    )
    if args.budget is not None:
        risk.total_budget = args.budget
    if args.fee_rate is not None:
        risk.fee_rate = args.fee_rate

    scoring = ScoringParams()
    if args.depth_frac is not None:
        scoring.order_depth_fraction = args.depth_frac

    filt = MarketFilter(
        min_mid=args.min_mid,
        max_mid=args.max_mid,
        min_spread=args.min_spread,
        min_days_to_expiry=args.min_days,
    )

    config = BotConfig(
        dry_run=args.dry_run or os.getenv("KALSHI_DRY_RUN", "false").lower() == "true",
        demo=args.demo or os.getenv("KALSHI_DEMO", "false").lower() == "true",
        scan_interval=args.scan_interval,
        risk=risk,
        scoring=scoring,
        market_filter=filt,
    )

    env_label = "DEMO" if config.demo else "LIVE"
    logging.getLogger(__name__).info(
        "Config: env=%s budget=$%.2f dry_run=%s scan=%ds",
        env_label, config.risk.total_budget, config.dry_run, config.scan_interval,
    )

    bot = KalshiBot(config, state_db=args.state_db)

    if args.close_all:
        bot.close_all()
    else:
        bot.run()


if __name__ == "__main__":
    main()
