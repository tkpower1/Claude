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
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


# ---------------------------------------------------------------------------
# Market-selection filters (prices on 0-1 probability scale internally)
# ---------------------------------------------------------------------------
@dataclass
class MarketFilter:
    # YES mid-price must be within this range (near 50/50)
    min_mid: float = 0.35
    max_mid: float = 0.65

    # Minimum bid-ask spread we require (probability units).
    # Fee break-even requires depth > 3.27¢/side, so spread > 2×3.27¢ = 6.54¢.
    # Use 0.07 (7¢) as the minimum to ensure positive expected value after fees.
    min_spread: float = 0.02

    # Maximum open interest we'll enter (USD)
    max_open_interest: float = 100_000.0

    # Require at least this many days until resolution
    min_days_to_expiry: int = 3

    # Maximum days until resolution.
    # Set to 0 to disable (trade markets at any horizon).
    # Set to 3 for the pre-resolution vol-spike paper-trade strategy:
    #   the 72h pre-resolution window has higher vol and shorter adverse-
    #   selection exposure — the core hypothesis being tested.
    max_days_to_expiry: int = 0

    # Only trade markets with this status
    allowed_statuses: tuple = ("open", "active")


# ---------------------------------------------------------------------------
# Scoring / pricing parameters
# ---------------------------------------------------------------------------
@dataclass
class ScoringParams:
    # Fraction of the spread to sit inside from mid (0 = at mid, 1 = at edge)
    order_depth_fraction: float = 0.40

    # Default spread window v if not derivable from market data.
    # Must satisfy: 2 * default_v * order_depth_fraction > fee_rate / (1 + fee_rate)
    # i.e. depth > 3.27¢ per side at 7% fee.  0.09 × 0.40 = 3.6¢ — fee-profitable.
    default_v: float = 0.09

    # Minimum ratio of short-term realized vol to long-term baseline before
    # we open a new position.  0.0 = disabled (always quote).
    # Set to 1.5 for the vol-spike paper-trade strategy: only quote when the
    # last 6h of realized vol is ≥ 1.5× the 7-day baseline.  This selects
    # the "pre-resolution spike" window where spreads widen above fee break-even.
    min_vol_ratio: float = 0.0


# ---------------------------------------------------------------------------
# Position / risk parameters
# ---------------------------------------------------------------------------
@dataclass
class RiskParams:
    # Total capital allocated to the bot (USD)
    total_budget: float = 1_000.0

    # Maximum share of budget in any single market
    max_market_fraction: float = 0.10   # sweep-optimised (was 0.15)

    # Kelly fraction multiplier (quarter-Kelly for safety)
    kelly_multiplier: float = 0.20   # sweep-optimised (was 0.25)

    # Maximum combined cost of YES + NO per contract pair.
    # Kalshi charges 7% fee on fills: break-even = 1 / (1 + 0.07) = 0.9346.
    # We set 0.93 to ensure a small positive margin after fees on every filled pair.
    # (1 - 0.93) - 0.07 * 0.93 = 0.07 - 0.0651 = +0.005 net per contract)
    max_fill_cost: float = 0.93

    # Minimum spread we need to bother market-making (probability units)
    min_target_spread: float = 0.04

    # Number of order levels (ladder) per side
    order_levels: int = 3

    # Gap between ladder levels (probability units)
    level_gap: float = 0.01

    # Maximum QUOTING age before cancellation and re-quote (seconds).
    # Sweep-optimised: 4h balances fill opportunity vs capital lock-up from one-sided fills.
    # Shorter age reduces adverse selection by repricing frequently at current market.
    max_order_age: int = 14_400   # 4 hours (sweep-optimised)

    # Minimum contract count per order (Kalshi minimum is 1)
    min_order_contracts: int = 1

    # Directional stop-loss for one-sided hedges (probability units).
    # If the unhedged side's market ask is this far above our hedge limit
    # (meaning the market has moved against us), close at mark-to-market
    # rather than holding until resolution.
    # Set to 1.0 to disable (rely on OU mean-reversion, recommended for backtesting).
    # For live trading set to 0.25-0.35 to cap tail losses on trending markets.
    hedge_stop_gap: float = 1.0   # disabled – rely on mean reversion

    # Pre-fill price-drift cancellation (probability units).
    # While a position is still QUOTING (neither side filled yet), if the
    # market mid has moved more than this from where we placed the order,
    # cancel and release budget immediately – before any fill can register.
    # Must be LARGER than depth + half_spread for fills to occur before cancellation.
    # With depth≈0.036 and typical half_spread≈0.04: fill_dist≈0.076 → use 0.15.
    # Set to 1.0 to disable.
    cancel_if_mid_drift: float = 0.15

    # Kalshi trading fee as a fraction of position cost per fill.
    # Kalshi charges approximately 7% of the cost of each filled contract.
    # e.g. buying 10 YES contracts at $0.48 each:
    #   fee = 0.07 × $0.48 × 10 = $0.336
    # Verify the current schedule at: https://kalshi.com/fees
    # Set to 0.0 to ignore fees (useful for gross P&L analysis).
    fee_rate: float = 0.07


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
