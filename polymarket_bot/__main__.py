"""
Entry point: python -m polymarket_bot [options]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import BotConfig, MarketFilter, RiskParams, ScoringParams
from .bot import PolymarketBot


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Silence noisy third-party loggers
    for lib in ("urllib3", "requests", "web3", "eth_account"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="polymarket_bot",
        description="Polymarket LP / Hedge Bot – three-scenario liquidity strategy",
    )

    # Operational
    p.add_argument("--dry-run", action="store_true",
                   help="Compute orders but never submit them")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   help="Logging verbosity (default: INFO)")
    p.add_argument("--scan-interval", type=int, default=60,
                   help="Seconds between market scans (default: 60)")

    # Budget / risk
    p.add_argument("--budget", type=float, default=None,
                   help="Total USDC budget (overrides env/config)")
    p.add_argument("--max-fill-cost", type=float, default=1.02,
                   help="Max YES+NO combined cost (default: 1.02)")
    p.add_argument("--kelly-mult", type=float, default=0.25,
                   help="Kelly multiplier, 0.25 = quarter-Kelly (default: 0.25)")
    p.add_argument("--order-levels", type=int, default=3,
                   help="Number of ladder levels per side (default: 3)")

    # Market filter
    p.add_argument("--min-mid", type=float, default=0.35,
                   help="Minimum YES mid price (default: 0.35)")
    p.add_argument("--max-mid", type=float, default=0.65,
                   help="Maximum YES mid price (default: 0.65)")
    p.add_argument("--min-spread", type=float, default=0.03,
                   help="Minimum bid-ask spread required (default: 0.03)")
    p.add_argument("--min-days", type=int, default=3,
                   help="Minimum days to expiry (default: 3)")

    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)

    # Load .env if present (optional convenience)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Build config from args + environment
    risk = RiskParams(
        max_fill_cost=args.max_fill_cost,
        kelly_multiplier=args.kelly_mult,
        order_levels=args.order_levels,
    )
    if args.budget is not None:
        risk.total_budget = args.budget

    filt = MarketFilter(
        min_mid=args.min_mid,
        max_mid=args.max_mid,
        min_spread=args.min_spread,
        min_days_to_expiry=args.min_days,
    )

    config = BotConfig(
        dry_run=args.dry_run or os.getenv("POLY_DRY_RUN", "false").lower() == "true",
        scan_interval=args.scan_interval,
        risk=risk,
        market_filter=filt,
    )

    logging.getLogger(__name__).info(
        "Config: budget=%.2f dry_run=%s scan=%ds",
        config.risk.total_budget, config.dry_run, config.scan_interval,
    )

    bot = PolymarketBot(config)
    bot.run()


if __name__ == "__main__":
    main()
