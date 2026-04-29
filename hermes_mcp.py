#!/usr/bin/env python3
"""MCP server for Hermes Agent.

Exposes the bot's control surface as tools Hermes can call. Reads and writes
the same config.json + data/ files the bot uses, so changes propagate on the
bot's next iteration. Stdio transport — Hermes spawns this process.

Run standalone for testing:
    python hermes_mcp.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT        = Path(__file__).parent.resolve()
CONFIG_PATH = ROOT / "config.json"
DATA_DIR    = ROOT / "data"
STATE_FILE  = DATA_DIR / "state.json"
MARKETS_DIR = DATA_DIR / "markets"
CAL_FILE    = DATA_DIR / "calibration.json"
AUDIT_LOG   = DATA_DIR / "agent_audit.log"

mcp = FastMCP("weatherbet")


# ---------- helpers ---------------------------------------------------------

def _load_cfg():
    return json.loads(CONFIG_PATH.read_text())

def _save_cfg(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def _load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def _load_markets():
    if not MARKETS_DIR.exists():
        return []
    out = []
    for p in MARKETS_DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            pass
    return out

def _audit(action, payload):
    DATA_DIR.mkdir(exist_ok=True)
    rec = {
        "ts":     datetime.now(timezone.utc).isoformat(),
        "action": action,
        **payload,
    }
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")

def _redact_cfg(cfg):
    """Hide secrets when returning config to the agent."""
    out = json.loads(json.dumps(cfg))  # deep copy
    w = out.get("wallet") or {}
    if w.get("private_key"):
        w["private_key"] = "***REDACTED***"
    return out


# ---------- read-only tools -------------------------------------------------

@mcp.tool()
def get_status() -> dict:
    """Bot status: balance, trade counts, mode, risk-cap snapshot, kill/pause flags."""
    cfg     = _load_cfg()
    state   = _load_state()
    markets = _load_markets()
    open_p  = [m for m in markets if m.get("position", {}).get("status") == "open"]
    risk    = cfg.get("risk", {})
    return {
        "mode":             "LIVE" if cfg.get("live_trading") else "PAPER",
        "balance":          state.get("balance"),
        "starting_balance": state.get("starting_balance"),
        "peak_balance":     state.get("peak_balance"),
        "wins":             state.get("wins", 0),
        "losses":           state.get("losses", 0),
        "total_trades":     state.get("total_trades", 0),
        "open_positions":   len(open_p),
        "kill_switch":      (DATA_DIR / Path(risk.get("kill_switch_file", "data/KILL_SWITCH")).name).exists(),
        "paused":           (DATA_DIR / Path(risk.get("pause_file", "data/PAUSED")).name).exists(),
        "risk":             risk,
    }


@mcp.tool()
def get_positions() -> list:
    """List all currently open positions with entry, latest price snapshot, and PnL."""
    out = []
    for m in _load_markets():
        pos = m.get("position")
        if not pos or pos.get("status") != "open":
            continue
        latest = None
        for o in m.get("all_outcomes", []):
            if o.get("market_id") == pos.get("market_id"):
                latest = o.get("bid", o.get("price"))
                break
        out.append({
            "city":          m.get("city"),
            "date":          m.get("date"),
            "market_id":     pos.get("market_id"),
            "yes_token_id":  pos.get("yes_token_id"),
            "bucket":        f"{pos.get('bucket_low')}-{pos.get('bucket_high')}",
            "entry_price":   pos.get("entry_price"),
            "latest_price":  latest,
            "shares":        pos.get("shares"),
            "cost":          pos.get("cost"),
            "ev_at_entry":   pos.get("ev"),
            "forecast_temp": pos.get("forecast_temp"),
            "forecast_src":  pos.get("forecast_src"),
            "opened_at":     pos.get("opened_at"),
            "order_id":      pos.get("order_id"),
        })
    return out


@mcp.tool()
def get_recent_trades(limit: int = 20) -> list:
    """Most recent resolved or closed positions, newest first."""
    closed = []
    for m in _load_markets():
        pos = m.get("position") or {}
        if pos.get("status") == "closed" or m.get("status") == "resolved":
            closed.append({
                "city":         m.get("city"),
                "date":         m.get("date"),
                "outcome":      m.get("resolved_outcome"),
                "pnl":          m.get("pnl") if m.get("pnl") is not None else pos.get("pnl"),
                "entry_price":  pos.get("entry_price"),
                "exit_price":   pos.get("exit_price"),
                "close_reason": pos.get("close_reason"),
                "closed_at":    pos.get("closed_at"),
            })
    closed.sort(key=lambda r: r.get("closed_at") or "", reverse=True)
    return closed[:limit]


@mcp.tool()
def get_calibration() -> dict:
    """Per-city/source forecast sigma values learned from history."""
    if CAL_FILE.exists():
        return json.loads(CAL_FILE.read_text())
    return {}


@mcp.tool()
def get_config() -> dict:
    """Full config with secrets redacted. Use for inspection only."""
    return _redact_cfg(_load_cfg())


# ---------- mutating tools (allowlisted) ------------------------------------

@mcp.tool()
def update_config(field: str, value: float, reason: str) -> dict:
    """Update a single bot tuning parameter. Only fields listed in
    `agent_config.editable_fields` are accepted. Risk caps and wallet config
    are read-only from the agent's perspective."""
    cfg = _load_cfg()
    allow = (cfg.get("agent_config") or {}).get("editable_fields") or []
    if field not in allow:
        return {"ok": False, "error": f"field '{field}' not in editable_fields", "allowed": allow}
    if field not in cfg:
        return {"ok": False, "error": f"field '{field}' not present in config"}
    old = cfg[field]
    try:
        if isinstance(old, int) and not isinstance(old, bool):
            cfg[field] = int(value)
        else:
            cfg[field] = float(value)
    except Exception as e:
        return {"ok": False, "error": f"value coercion failed: {e}"}
    _save_cfg(cfg)
    _audit("update_config", {"field": field, "old": old, "new": cfg[field], "reason": reason})
    return {"ok": True, "field": field, "old": old, "new": cfg[field]}


@mcp.tool()
def pause(reason: str) -> dict:
    """Halt new entries. Open positions and exits continue to be managed."""
    cfg  = _load_cfg()
    path = Path((cfg.get("risk") or {}).get("pause_file", "data/PAUSED"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"paused_at": datetime.now(timezone.utc).isoformat(), "reason": reason}))
    _audit("pause", {"reason": reason})
    return {"ok": True, "paused": True}


@mcp.tool()
def resume(reason: str = "") -> dict:
    """Lift the pause flag — new entries can resume."""
    cfg  = _load_cfg()
    path = Path((cfg.get("risk") or {}).get("pause_file", "data/PAUSED"))
    if path.exists():
        path.unlink()
    _audit("resume", {"reason": reason})
    return {"ok": True, "paused": False}


@mcp.tool()
def set_kill_switch(reason: str) -> dict:
    """Trip the kill switch. Bot stops opening new positions immediately."""
    cfg  = _load_cfg()
    path = Path((cfg.get("risk") or {}).get("kill_switch_file", "data/KILL_SWITCH"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tripped_at": datetime.now(timezone.utc).isoformat(), "reason": reason}))
    _audit("set_kill_switch", {"reason": reason})
    return {"ok": True, "kill_switch": True}


@mcp.tool()
def reset_kill_switch(reason: str) -> dict:
    """Clear the kill switch."""
    cfg  = _load_cfg()
    path = Path((cfg.get("risk") or {}).get("kill_switch_file", "data/KILL_SWITCH"))
    if path.exists():
        path.unlink()
    _audit("reset_kill_switch", {"reason": reason})
    return {"ok": True, "kill_switch": False}


@mcp.tool()
def request_close_position(market_id: str, reason: str) -> dict:
    """Mark an open position for closure on the bot's next monitor tick.
    Writes a flag file the bot reads — does not directly touch the exchange."""
    DATA_DIR.mkdir(exist_ok=True)
    flags = DATA_DIR / "close_requests.json"
    queue = []
    if flags.exists():
        try:
            queue = json.loads(flags.read_text())
        except Exception:
            queue = []
    queue.append({"market_id": market_id, "reason": reason,
                  "queued_at": datetime.now(timezone.utc).isoformat()})
    flags.write_text(json.dumps(queue, indent=2))
    _audit("request_close_position", {"market_id": market_id, "reason": reason})
    return {"ok": True, "queued": True, "market_id": market_id}


@mcp.tool()
def get_audit_log(limit: int = 50) -> list:
    """Recent agent actions, newest first."""
    if not AUDIT_LOG.exists():
        return []
    lines = AUDIT_LOG.read_text().splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return list(reversed(out))


if __name__ == "__main__":
    mcp.run()
