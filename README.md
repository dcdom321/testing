# 🌤 WeatherBet — Polymarket Weather Trading Bot

Automated weather market trading bot for Polymarket. Finds mispriced temperature outcomes using real forecast data from multiple sources across 20 cities worldwide.

No SDK. No black box. Pure Python.

---

## Versions

### `bot_v1.py` — Base Bot
The foundation. Scans 6 US cities, fetches forecasts from NWS using airport station coordinates, finds matching temperature buckets on Polymarket, and enters trades when the market price is below the entry threshold.

No math, no complexity. Just the core logic — good for understanding how the system works.

### `weatherbet.py` — Full Bot (current)
Everything in v1, plus:
- **20 cities** across 4 continents (US, Europe, Asia, South America, Oceania)
- **3 forecast sources** — ECMWF (global), HRRR/GFS (US, hourly), METAR (real-time observations)
- **Expected Value** — skips trades where the math doesn't work
- **Kelly Criterion** — sizes positions based on edge strength
- **Stop-loss + trailing stop** — 20% stop, moves to breakeven at +20%
- **Slippage filter** — skips markets with spread > $0.03
- **Self-calibration** — learns forecast accuracy per city over time
- **Full data storage** — every forecast snapshot, trade, and resolution saved to JSON

---

## How It Works

Polymarket runs markets like "Will the highest temperature in Chicago be between 46–47°F on March 7?" These markets are often mispriced — the forecast says 78% likely but the market is trading at 8 cents.

The bot:
1. Fetches forecasts from ECMWF and HRRR via Open-Meteo (free, no key required)
2. Gets real-time observations from METAR airport stations
3. Finds the matching temperature bucket on Polymarket
4. Calculates Expected Value — only enters if the math is positive
5. Sizes the position using fractional Kelly Criterion
6. Monitors stops every 10 minutes, full scan every hour
7. Auto-resolves markets by querying Polymarket API directly

---

## Why Airport Coordinates Matter

Most bots use city center coordinates. That's wrong.

Every Polymarket weather market resolves on a specific airport station. NYC resolves on LaGuardia (KLGA), Dallas on Love Field (KDAL) — not DFW. The difference between city center and airport can be 3–8°F. On markets with 1–2°F buckets, that's the difference between the right trade and a guaranteed loss.

| City | Station | Airport |
|------|---------|---------|
| NYC | KLGA | LaGuardia |
| Chicago | KORD | O'Hare |
| Miami | KMIA | Miami Intl |
| Dallas | KDAL | Love Field |
| Seattle | KSEA | Sea-Tac |
| Atlanta | KATL | Hartsfield |
| London | EGLC | London City |
| Tokyo | RJTT | Haneda |
| ... | ... | ... |

---

## Installation
```bash
git clone https://github.com/alteregoeth-ai/weatherbot
cd weatherbot
pip install requests
```

Create `config.json` in the project folder:
```json
{
  "balance": 10000.0,
  "max_bet": 20.0,
  "min_ev": 0.05,
  "max_price": 0.45,
  "min_volume": 2000,
  "min_hours": 2.0,
  "max_hours": 72.0,
  "kelly_fraction": 0.25,
  "max_slippage": 0.03,
  "scan_interval": 3600,
  "calibration_min": 30,
  "vc_key": "YOUR_VISUAL_CROSSING_KEY",
  "weatherapi_key": "YOUR_WEATHERAPI_KEY",
  "weather_provider": "visualcrossing"
}
```

Used to fetch actual temperatures after market resolution. You can use either
provider — set `weather_provider` to `"visualcrossing"` or `"weatherapi"`. The
bot falls back to the other provider if the primary fails and a key is
configured for it. Leave a key as `"YOUR_KEY_HERE"` (or empty) to disable it.

- Visual Crossing — free key at visualcrossing.com
- WeatherAPI — free key at weatherapi.com

---

## Usage
```bash
python weatherbet.py           # start the bot — scans every hour
python weatherbet.py status    # balance and open positions
python weatherbet.py report    # full breakdown of all resolved markets
```

---

## Data Storage

All data is saved to `data/markets/` — one JSON file per market. Each file contains:
- Hourly forecast snapshots (ECMWF, HRRR, METAR)
- Market price history
- Position details (entry, stop, PnL)
- Final resolution outcome

This data is used for self-calibration — the bot learns forecast accuracy per city over time and adjusts position sizing accordingly.

---

## APIs Used

| API | Auth | Purpose |
|-----|------|---------|
| Open-Meteo | None | ECMWF + HRRR forecasts |
| Aviation Weather (METAR) | None | Real-time station observations |
| Polymarket Gamma | None | Market data |
| Visual Crossing | Free key | Historical temps for resolution |
| WeatherAPI | Free key | Historical temps for resolution (alternative) |

---

## Live Trading

The bot ships in **paper mode** by default (`live_trading: false`). Flipping it
to `true` and providing wallet credentials enables real Polymarket execution
via [py-clob-client](https://github.com/Polymarket/py-clob-client).

**Wallet config** (in `config.json` under `wallet`):
- `private_key` — raw hex private key, **OR**
- `private_key_env: POLYMARKET_PRIVATE_KEY` — name of env var that holds the key (preferred)
- `funder_address` / `funder_address_env` — your Polymarket proxy/funder address
- `signature_type` — `2` for the Polymarket UI proxy wallet (default), `0` for a raw EOA

**Risk caps** (in `config.json` under `risk`) are enforced before every entry:
- `max_per_trade` — hard cap on a single position's USD cost
- `max_daily_loss` — circuit breaker that halts new entries for the rest of the UTC day
- `max_concurrent_positions` — total open positions
- `max_total_exposure` — sum of cost across all open positions
- `kill_switch_file` / `pause_file` — flag files Hermes (or you) can drop to stop entries

These four caps cannot be raised by the agent at runtime. They are the floor
your funds are protected by.

**One-time wallet check**:
```bash
python setup_wallet.py
```
Verifies API creds derive cleanly and the bot can read your open orders. Does
not place orders. Make sure USDC + CTF allowances are approved on Polygon for
the Polymarket exchange contracts.

## Hermes Agent (MCP)

`hermes_mcp.py` is a Model Context Protocol server that exposes the bot's
control surface to [Hermes Agent](https://hermes-agent.nousresearch.com/).
Hermes can monitor history and adjust tuning parameters between runs.

Tools exposed:
- Read-only: `get_status`, `get_positions`, `get_recent_trades`, `get_calibration`, `get_config`, `get_audit_log`
- Tuning: `update_config(field, value, reason)` — only fields in `agent_config.editable_fields` (e.g. `min_ev`, `kelly_fraction`)
- Operational: `pause`, `resume`, `set_kill_switch`, `reset_kill_switch`, `request_close_position`

Every mutation is appended to `data/agent_audit.log`. Risk caps and wallet
config are deliberately not editable from MCP.

Add this to your Hermes config (typically `~/.hermes/config.toml` or via
`hermes mcp add`):
```toml
[mcp_servers.weatherbet]
command = "/opt/weatherbet/.venv/bin/python"
args    = ["/opt/weatherbet/hermes_mcp.py"]
```

## Deployment (Ubuntu VPS)

One-shot installer:
```bash
git clone <repo> weatherbet && cd weatherbet
sudo bash deploy/install.sh
sudo nano /opt/weatherbet/.env          # paste private key + funder address
sudo nano /opt/weatherbet/config.json   # set live_trading + risk caps
sudo -u weatherbet /opt/weatherbet/.venv/bin/python /opt/weatherbet/setup_wallet.py
sudo systemctl start weatherbet
journalctl -u weatherbet -f
```

The unit runs under a dedicated `weatherbet` user with hardening
(`ProtectSystem=strict`, `NoNewPrivileges`, etc.). The `.env` file is mode
`600`. Data is persisted to `/opt/weatherbet/data/`.

Docker alternative:
```bash
cp .env.example .env && nano .env
docker compose up -d
docker compose logs -f
```

## Disclaimer

This is not financial advice. Prediction markets carry real risk. Run with
`live_trading: false` and conservative `risk` caps until you've verified the
behavior end-to-end. Live mode places real orders against your funds.
