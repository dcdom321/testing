"""Pre-trade risk gating.

Hard caps live in config.json under `risk`. Caps that protect funds (per-trade,
daily-loss, total-exposure, concurrent positions) cannot be raised by Hermes at
runtime — those are deliberately read-only from the agent's perspective. The
agent can still pause/resume and hit the kill switch.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = {
    "max_per_trade":             10.0,
    "max_daily_loss":            50.0,
    "max_concurrent_positions":  5,
    "max_total_exposure":       100.0,
    "kill_switch_file":         "data/KILL_SWITCH",
    "pause_file":               "data/PAUSED",
    "flatten_on_kill":          False,
}


class RiskGate:
    def __init__(self, cfg, data_dir: Path):
        r = {**DEFAULTS, **(cfg.get("risk") or {})}
        self.max_per_trade        = float(r["max_per_trade"])
        self.max_daily_loss       = float(r["max_daily_loss"])
        self.max_concurrent       = int(r["max_concurrent_positions"])
        self.max_total_exposure   = float(r["max_total_exposure"])
        self.flatten_on_kill      = bool(r["flatten_on_kill"])
        self.kill_switch_file     = Path(r["kill_switch_file"])
        self.pause_file           = Path(r["pause_file"])
        self.data_dir             = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -- agent-facing controls ---------------------------------------------
    def kill_active(self) -> bool:
        return self.kill_switch_file.exists()

    def paused(self) -> bool:
        return self.pause_file.exists()

    def pause(self, reason: str = ""):
        self.pause_file.parent.mkdir(parents=True, exist_ok=True)
        self.pause_file.write_text(json.dumps({
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "reason":    reason,
        }))

    def resume(self):
        if self.pause_file.exists():
            self.pause_file.unlink()

    def trip_kill_switch(self, reason: str = ""):
        self.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
        self.kill_switch_file.write_text(json.dumps({
            "tripped_at": datetime.now(timezone.utc).isoformat(),
            "reason":     reason,
        }))

    def reset_kill_switch(self):
        if self.kill_switch_file.exists():
            self.kill_switch_file.unlink()

    # -- gate ---------------------------------------------------------------
    def check_entry(self, size, open_positions):
        """Return None if allowed, else a refusal reason."""
        if self.kill_active():
            return "kill_switch_active"
        if self.paused():
            return "paused"
        if size > self.max_per_trade:
            return f"size ${size:.2f} > max_per_trade ${self.max_per_trade:.2f}"
        if len(open_positions) >= self.max_concurrent:
            return f"concurrent {len(open_positions)} >= max {self.max_concurrent}"
        exposure = sum(float(p.get("cost", 0)) for p in open_positions)
        if exposure + size > self.max_total_exposure:
            return f"exposure ${exposure + size:.2f} > max ${self.max_total_exposure:.2f}"
        loss = self.todays_loss(open_positions)
        if loss >= self.max_daily_loss:
            return f"daily loss ${loss:.2f} >= cap ${self.max_daily_loss:.2f}"
        return None

    def todays_loss(self, all_markets) -> float:
        """Sum of negative realized PnL on positions closed today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        loss = 0.0
        for m in all_markets:
            pos = m.get("position") or {}
            closed_at = pos.get("closed_at") or ""
            pnl = pos.get("pnl")
            if not closed_at or pnl is None:
                continue
            if closed_at[:10] == today and pnl < 0:
                loss += -float(pnl)
        return loss

    def snapshot(self):
        """Read-only state for monitoring (Hermes calls this)."""
        return {
            "kill_switch":          self.kill_active(),
            "paused":               self.paused(),
            "max_per_trade":        self.max_per_trade,
            "max_daily_loss":       self.max_daily_loss,
            "max_concurrent":       self.max_concurrent,
            "max_total_exposure":   self.max_total_exposure,
            "kill_switch_file":     str(self.kill_switch_file),
            "pause_file":           str(self.pause_file),
        }
