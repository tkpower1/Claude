# Polymarket LP / Hedge Bot

Automated liquidity-provision strategy on Polymarket that **earns rewards by
posting YES and NO limit orders** and hedges instantly if one side fills.

---

## Strategy overview

The bot exploits Polymarket's liquidity-reward programme. Orders sitting inside
the reward window earn a stream of daily USDC regardless of whether they ever
fill. Three scenarios cover all outcomes:

| Scenario | What happens | Result |
|---|---|---|
| **1 – Neither fills** | Both orders stay live. | Collect daily rewards. _Main income._ |
| **2 – One side fills** | The bot immediately places a hedge on the opposite side so that `YES_price + NO_price ≤ $1.02`. | Guaranteed profit at resolution. |
| **3 – Both fill** | Fully hedged: one share pays $1, one pays $0. | Net profit = `$1.00 − (p_YES + p_NO) > 0`. |

---

## Scoring function (paper §3.1)

```
S(s) = ((v − s) / v)² · b
```

| Symbol | Meaning |
|---|---|
| `s` | Distance of our order from the mid price |
| `v` | Reward window (max spread from mid) |
| `b` | Market multiplier |

Our reward share: `R_i = (S_i / Σ S_j) · P_daily`

Orders closer to mid score higher. The bot places a configurable fraction of
`v` from mid (default 40 %) to balance score against fill risk.

---

## Project layout

```
polymarket_bot/
├── __init__.py          # public API
├── __main__.py          # CLI entry point (python -m polymarket_bot)
├── bot.py               # main orchestrator / event loop
├── client.py            # Polymarket CLOB + Gamma API wrapper
├── config.py            # all knobs (BotConfig, RiskParams, …)
├── market_selector.py   # filter & rank markets
├── order_manager.py     # place orders, detect fills, trigger hedges
├── position_sizer.py    # Kelly criterion + budget tracker
├── rewards.py           # scoring, PnL scenarios
└── requirements.txt
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r polymarket_bot/requirements.txt
```

### 2. Set credentials

```bash
export POLY_PRIVATE_KEY="your_ethereum_private_key_hex"
export POLY_API_KEY="your_clob_api_key"
export POLY_API_SECRET="your_clob_api_secret"
export POLY_API_PASSPHRASE="your_clob_api_passphrase"
```

Or put them in a `.env` file in the project root (loaded automatically if
`python-dotenv` is installed).

### 3. Run in dry-run mode first

```bash
python -m polymarket_bot --dry-run --log-level DEBUG
```

This scans markets, computes orders, and prints everything without submitting.

### 4. Go live

```bash
python -m polymarket_bot --budget 500 --scan-interval 60
```

---

## CLI options

```
--dry-run              Never submit orders (safe testing mode)
--budget FLOAT         Total USDC to deploy  [default: env / 1000]
--max-fill-cost FLOAT  Max YES+NO combined cost  [default: 1.02]
--kelly-mult FLOAT     Kelly multiplier (0.25 = quarter-Kelly)  [default: 0.25]
--order-levels INT     Ladder levels per side  [default: 3]
--min-mid FLOAT        Min YES mid price  [default: 0.35]
--max-mid FLOAT        Max YES mid price  [default: 0.65]
--min-spread FLOAT     Min bid-ask spread  [default: 0.03]
--min-days INT         Min days to expiry  [default: 3]
--log-level LEVEL      DEBUG / INFO / WARNING / ERROR  [default: INFO]
--scan-interval INT    Seconds between market scans  [default: 60]
```

---

## Market selection criteria

| Filter | Default | Rationale |
|---|---|---|
| Mid price | 0.35 – 0.65 | Near 50/50 → symmetric risk, widest reward window |
| Spread | ≥ 0.03 | Room for our orders between best bid and ask |
| Open interest | ≤ $100 k | Less competition for reward share |
| Days to expiry | ≥ 3 | Enough time to collect meaningful rewards |

---

## Position sizing

Kelly fraction (quarter-Kelly default):

```
f* = (2p − 1) / (1 − p)   ×   kelly_multiplier
```

Budget per market is capped at `max_market_fraction` (15 %) of total budget.
Order size per level = `budget_for_market / (2 × levels)`.

---

## Risk controls

- Orders only placed when `YES_price + NO_price ≤ max_fill_cost` (default $1.02)
- Stale orders (> 1 hour) cancelled and re-quoted at current mid
- Hard budget cap per market (`max_market_fraction`)
- Graceful shutdown on SIGINT / SIGTERM (no orphan orders)

---

## Getting API credentials

1. Create a wallet at [polymarket.com](https://polymarket.com)
2. Derive your L2 API key: follow the [Polymarket CLOB docs](https://docs.polymarket.com/#create-api-key)
3. Fund your account with USDC on Polygon

---

## Disclaimer

This software is provided for educational purposes. Prediction-market trading
carries financial risk. Always test with small amounts first.
