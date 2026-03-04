"""
Configuration for Kalshi market-making bot.

Environment variables (set in .env or shell):
  KALSHI_API_KEY_ID    – API key ID from Kalshi account settings
  KALSHI_PRIVATE_KEY   – RSA private key (PEM string or path to .pem file)
  KALSHI_DEMO          – "true" to use demo environment (default: false)
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Network / endpoint constants
# ---------------------------------------------------------------------------
KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


# ---------------------------------------------------------------------------
# Market-selection filters (prices on 0-1 probability scale internally)
# ---------------------------------------------------------------------------
@dataclass
class MarketFilter:
    # YES mid-price must be within this range (near 50/50)
    min_mid: float = 0.35
    max_mid: float = 0.65

    # Minimum bid-ask spread we require (probability units, e.g. 0.03 = 3¢)
    min_spread: float = 0.03

    # Maximum open interest we'll enter (USD)
    max_open_interest: float = 100_000.0

    # Require at least this many days until resolution
    min_days_to_expiry: int = 3

    # Only trade markets with this status
    allowed_statuses: tuple = ("open",)


# ---------------------------------------------------------------------------
# Scoring / pricing parameters
# ---------------------------------------------------------------------------
@dataclass
class ScoringParams:
    # Fraction of the spread to sit inside from mid (0 = at mid, 1 = at edge)
    order_depth_fraction: float = 0.40

    # Default spread window v if not derivable from market data
    default_v: float = 0.05


# ---------------------------------------------------------------------------
# Position / risk parameters
# ---------------------------------------------------------------------------
@dataclass
class RiskParams:
    # Total capital allocated to the bot (USD)
    total_budget: float = 1_000.0

    # Maximum share of budget in any single market
    max_market_fraction: float = 0.15

    # Kelly fraction multiplier (quarter-Kelly for safety)
    kelly_multiplier: float = 0.25

    # Maximum combined cost of YES + NO per contract pair
    # At resolution one pays $1 and one pays $0, so combined ≤ $1.00.
    # We allow a small premium for fees / edge: 1.02 = 2¢ max cost above parity.
    max_fill_cost: float = 1.02

    # Minimum spread we need to bother market-making (probability units)
    min_target_spread: float = 0.04

    # Number of order levels (ladder) per side
    order_levels: int = 3

    # Gap between ladder levels (probability units)
    level_gap: float = 0.01

    # Maximum position age before cancellation and re-quote (seconds)
    max_order_age: int = 3600    # 1 hour

    # Minimum contract count per order (Kalshi minimum is 1)
    min_order_contracts: int = 1


# ---------------------------------------------------------------------------
# Operational parameters
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    # API credentials (pulled from environment)
    api_key_id: str = field(
        default_factory=lambda: os.getenv("KALSHI_API_KEY_ID", "")
    )
    # Private key: either a PEM string or a path to a .pem file
    private_key_pem: str = field(
        default_factory=lambda: os.getenv("KALSHI_PRIVATE_KEY", "")
    )

    # Use demo environment?
    demo: bool = field(
        default_factory=lambda: os.getenv("KALSHI_DEMO", "false").lower() == "true"
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
        default_factory=lambda: os.getenv("KALSHI_DRY_RUN", "false").lower() == "true"
    )

    @property
    def api_base(self) -> str:
        return KALSHI_DEMO_BASE if self.demo else KALSHI_API_BASE

    def validate(self) -> None:
        """Raise if required credentials are missing (unless dry_run)."""
        if self.dry_run:
            return
        missing = []
        if not self.api_key_id:
            missing.append("KALSHI_API_KEY_ID")
        if not self.private_key_pem:
            missing.append("KALSHI_PRIVATE_KEY")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

    def load_private_key(self):
        """Return the RSA private key object (cryptography.hazmat)."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = self.private_key_pem.strip()
        # If it looks like a file path rather than a PEM block, read the file
        if not pem.startswith("-----"):
            with open(pem, "rb") as f:
                pem = f.read()
        else:
            pem = pem.encode()
        return load_pem_private_key(pem, password=None)


# Module-level default instance
DEFAULT_CONFIG = BotConfig()
