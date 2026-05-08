# Copybot — Polymarket copy-trading bot

Monitors target Polymarket trader wallets and copies their trades according to
configurable risk rules. Paper-trading is the default; real trading is gated
behind two independent flags so a single misconfiguration can't deploy real
money.

> **Risk warning.** Prediction markets carry real risk. This bot can lose
> every cent it places. Run in paper mode for days, audit the dry-run report,
> and tighten the caps before flipping anything real.

## How it works

Each tick the bot:

1. **Ingests** — calls `data-api.polymarket.com/trades?user=<wallet>` per
   target, advancing a per-wallet cursor so the same trade is never seen
   twice.
2. **Normalizes** — rejects malformed payloads (bad price, missing tx hash,
   etc.).
3. **Risk-gates** — runs the trade through ordered rules; the first failure
   short-circuits with a stable `rule` identifier. Every rejection is stored
   so the dry-run report can explain itself.
4. **Executes** — `PaperEngine` records a simulated fill at the current
   best-ask/bid; `ExecutionEngine` (real mode) calls the parent
   `polymarket_client` which wraps `py-clob-client`.
5. **Notifies** — Discord and/or Telegram, both optional. Silent if creds
   aren't set.

State lives in SQLite at `copybot/data/copybot.db` (configurable). Schema is
applied via idempotent migrations on startup.

## Risk rules

Evaluated in this order (cheap fail-fast first; the single Gamma fetch is
gated behind every local rule):

| Rule                          | Source         | Notes |
|-------------------------------|----------------|-------|
| `kill_switch_active`          | local file     | `data/KILL_SWITCH` exists |
| `paused`                      | local file     | `data/PAUSED` exists |
| `sell_without_position`       | DB             | We don't short-cover trades we never copied |
| `market_blacklisted`          | config         | |
| `market_not_whitelisted`      | config         | Only when whitelist is non-empty |
| `max_trade_usdc`              | config         | Cap or skip per `cap_oversize_trades` |
| `max_daily_loss`              | DB             | Today's realized loss vs. cap |
| `max_market_exposure`         | DB             | Net exposure per `condition_id` |
| `market_data_unavailable`     | network        | Snapshot fetch failed |
| `invalid_order_book`          | snapshot       | bid/ask sanity |
| `min_liquidity`               | snapshot       | |
| `max_price_move_after_target` | snapshot       | |
| `max_slippage`                | snapshot       | |

## CLI

```
python -m copybot init-db
python -m copybot run
python -m copybot status
python -m copybot dry-run-report --hours 24
python -m copybot pause --reason "ops"
python -m copybot resume
python -m copybot kill --reason "anomaly"
python -m copybot unkill
```

The dry-run report shows: target trades observed, copied count, skipped
counts grouped by rule, realized PnL, top markets by exposure, error count.

## Setup (Ubuntu / VPS)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp copybot/config.yaml.example copybot/config.yaml
cp .env.example .env

# Edit copybot/config.yaml: add at least one target wallet
# (Optional) Edit .env to add Discord/Telegram webhook + bot token

python -m copybot init-db
python -m copybot run            # paper mode, default
```

## Going live (only after paper validation)

Real-mode requires **both**:

1. `real_trading_enabled: true` in `copybot/config.yaml`
2. `REAL_TRADING_ENABLED=true` in `.env` (or environment)

If either is missing the bot stays in paper mode and prints a warning. The
parent `polymarket_client.from_config` adds a third gate: if no private key
is configured it falls back to a no-op stub client and logs the reason. This
is intentional — three separate things have to be right before money moves.

```bash
# In .env (mode 600):
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_FUNDER_ADDRESS=0x...
REAL_TRADING_ENABLED=true
```

USDC and CTF allowances on Polygon must be approved for the Polymarket
exchange contracts. If you've ever traded on the Polymarket UI with this
proxy wallet they already are.

## Tests

```
pip install pytest
pytest copybot/tests -v
```

Risk engine tests cover every rule, the ordering invariant, oversize cap
behavior, kill-switch and pause short-circuits, and the boundary conditions
on slippage / price-drift / liquidity.

## Operational flags

- Drop `copybot/data/KILL_SWITCH` to halt new entries instantly.
- Drop `copybot/data/PAUSED` for a temporary halt.
- `python -m copybot kill --reason ...` and `pause --reason ...` create these
  with a JSON payload recording when and why.
