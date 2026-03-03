"""
Polymarket LP / Hedge Trading Bot
==================================

Implements the three-scenario liquidity provision strategy:

  Scenario 1 – Neither fills  : collect daily rewards (main income)
  Scenario 2 – One side fills : immediate hedge; YES + NO ≤ $1.02 → profit
  Scenario 3 – Both fill      : fully hedged; $1 payout closes position

Usage
-----
    python -m polymarket_bot                   # run with env-var config
    python -m polymarket_bot --dry-run         # simulate without placing orders
    python -m polymarket_bot --help            # show all options
"""

from .bot import PolymarketBot, run_bot
from .config import BotConfig, MarketFilter, RiskParams, ScoringParams

__all__ = [
    "PolymarketBot",
    "run_bot",
    "BotConfig",
    "MarketFilter",
    "RiskParams",
    "ScoringParams",
]
