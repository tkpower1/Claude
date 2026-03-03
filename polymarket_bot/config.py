"""
Configuration for Polymarket LP / hedging bot.

Environment variables (set in .env or shell):
  POLY_PRIVATE_KEY   – Ethereum private key (hex, no 0x prefix)
  POLY_API_KEY       – L2 CLOB API key
  POLY_API_SECRET    – L2 CLOB API secret
  POLY_API_PASSPHRASE– L2 CLOB API passphrase
  POLY_FUNDER        – Funder address (optional, defaults to derived address)
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Network / endpoint constants
# ---------------------------------------------------------------------------
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
POLYGON_RPC = "https://polygon-rpc.com"
CHAIN_ID = 137  # Polygon mainnet

# ---------------------------------------------------------------------------
# Market-selection filters (all prices in USDC cents, i.e. 0-100 scale)
# ---------------------------------------------------------------------------
@dataclass
class MarketFilter:
    # Mid-price must be within this range to qualify (near 50/50)
    min_mid: float = 0.35
    max_mid: float = 0.65

    # Minimum bid-ask spread we require (in probability units, e.g. 0.03 = 3¢)
    min_spread: float = 0.03

    # Maximum open interest we'll enter (USDC). Avoids already-crowded books.
    max_open_interest: float = 100_000.0

    # Require at least this many days until resolution
    min_days_to_expiry: int = 3

    # Skip markets with active-order count above this (too competitive)
    max_maker_orders: int = 200


# ---------------------------------------------------------------------------
# Scoring-function parameters  (paper §3.1)
#
#   S(s) = ((v - s) / v)² · b
#
#   v – maximum allowed spread from mid (probability units)
#   b – market multiplier (fetched from API; default here is a safe fallback)
# ---------------------------------------------------------------------------
@dataclass
class ScoringParams:
    # Default max-spread window v if the market doesn't specify one
    default_v: float = 0.05      # 5¢ from mid
    # Fraction of v to sit our order at (0 = at mid, 1 = at edge)
    # Paper shows score peaks toward mid, so we sit at 40 % of v
    order_depth_fraction: float = 0.40


# ---------------------------------------------------------------------------
# Position / risk parameters
# ---------------------------------------------------------------------------
@dataclass
class RiskParams:
    # Total capital allocated to the bot (USDC)
    total_budget: float = 1_000.0

    # Maximum share of budget in any single market
    max_market_fraction: float = 0.15

    # Kelly fraction multiplier (quarter-Kelly for safety)
    kelly_multiplier: float = 0.25

    # Maximum combined cost of YES + NO per share pair (profit threshold)
    max_fill_cost: float = 1.02   # $1.02 total for a $1.00 payout

    # Minimum daily reward-rate (USDC / $ deployed) to bother with a market
    min_daily_reward_rate: float = 0.001   # 0.1 % / day

    # Number of order levels (ladder) per side
    order_levels: int = 3

    # Gap between ladder levels (probability units)
    level_gap: float = 0.01

    # Maximum position age before cancellation and re-quote (seconds)
    max_order_age: int = 3600    # 1 hour


# ---------------------------------------------------------------------------
# Operational parameters
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    # API credentials (pulled from environment)
    private_key: str = field(
        default_factory=lambda: os.getenv("POLY_PRIVATE_KEY", "")
    )
    api_key: str = field(
        default_factory=lambda: os.getenv("POLY_API_KEY", "")
    )
    api_secret: str = field(
        default_factory=lambda: os.getenv("POLY_API_SECRET", "")
    )
    api_passphrase: str = field(
        default_factory=lambda: os.getenv("POLY_API_PASSPHRASE", "")
    )
    funder: str = field(
        default_factory=lambda: os.getenv("POLY_FUNDER", "")
    )

    # Sub-configs
    market_filter: MarketFilter = field(default_factory=MarketFilter)
    scoring: ScoringParams = field(default_factory=ScoringParams)
    risk: RiskParams = field(default_factory=RiskParams)

    # Main loop cadence (seconds between market scans)
    scan_interval: int = 60

    # How often to log a full state report (seconds)
    report_interval: int = 300

    # Dry-run: compute orders but never actually submit
    dry_run: bool = field(
        default_factory=lambda: os.getenv("POLY_DRY_RUN", "false").lower() == "true"
    )

    def validate(self) -> None:
        """Raise if required credentials are missing (unless dry_run)."""
        if self.dry_run:
            return
        missing = [
            k for k, v in {
                "POLY_PRIVATE_KEY": self.private_key,
                "POLY_API_KEY": self.api_key,
                "POLY_API_SECRET": self.api_secret,
                "POLY_API_PASSPHRASE": self.api_passphrase,
            }.items() if not v
        ]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )


# Module-level default instance – import and override as needed
DEFAULT_CONFIG = BotConfig()
