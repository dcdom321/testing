"""Pre-trade risk gate.

Cheap fail-fast checks first; the single Gamma fetch happens only after every
local rule has passed. Returns `Approve(...)` or `Skip(rule, ...)`.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import persistence
from .config import Config, RiskCfg
from .market_data import MarketDataProvider
from .models import Approve, MarketSnapshot, Skip, Trade


# Stable rule identifiers — used as DB keys and notifier labels.
R_KILL_SWITCH        = "kill_switch_active"
R_PAUSED             = "paused"
R_SELL_NO_POSITION   = "sell_without_position"
R_BLACKLISTED        = "market_blacklisted"
R_NOT_WHITELISTED    = "market_not_whitelisted"
R_MAX_TRADE          = "max_trade_usdc"
R_DAILY_LOSS         = "max_daily_loss"
R_MARKET_EXPOSURE    = "max_market_exposure"
R_MARKET_DATA_DOWN   = "market_data_unavailable"
R_LIQUIDITY          = "min_liquidity"
R_PRICE_MOVE         = "max_price_move_after_target"
R_SLIPPAGE           = "max_slippage"
R_BAD_BOOK           = "invalid_order_book"


class CopyRiskGate:
    """Deciders + kill/pause file controls. Single-threaded."""

    def __init__(self, cfg: Config, conn: sqlite3.Connection,
                 market_data: MarketDataProvider):
        self.cfg = cfg
        self.risk: RiskCfg = cfg.risk
        self.conn = conn
        self.market_data = market_data
        self.kill_switch_file = Path(self.risk.kill_switch_file)
        self.pause_file = Path(self.risk.pause_file)
        self.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)

    # -- agent-facing controls ---------------------------------------------
    def kill_active(self) -> bool:
        return self.kill_switch_file.exists()

    def paused(self) -> bool:
        return self.pause_file.exists()

    def pause(self, reason: str = ""):
        self.pause_file.write_text(json.dumps({
            "paused_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
        }))

    def resume(self):
        if self.pause_file.exists():
            self.pause_file.unlink()

    def trip_kill_switch(self, reason: str = ""):
        self.kill_switch_file.write_text(json.dumps({
            "tripped_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
        }))

    def reset_kill_switch(self):
        if self.kill_switch_file.exists():
            self.kill_switch_file.unlink()

    def snapshot(self) -> dict:
        return {
            "kill_switch":              self.kill_active(),
            "paused":                   self.paused(),
            "copy_trade_percent":       self.risk.copy_trade_percent,
            "max_trade_usdc":           self.risk.max_trade_usdc,
            "max_daily_loss_usdc":      self.risk.max_daily_loss_usdc,
            "max_market_exposure_usdc": self.risk.max_market_exposure_usdc,
            "max_slippage_percent":     self.risk.max_slippage_percent,
            "max_price_move_percent":   self.risk.max_price_move_after_target,
            "min_liquidity_usdc":       self.risk.min_market_liquidity_usdc,
            "blacklist_n":              len(self.risk.blacklist_condition_ids),
            "whitelist_n":              len(self.risk.whitelist_condition_ids),
        }

    # -- core decision ------------------------------------------------------
    def evaluate(self, trade: Trade, copy_percent: float) -> object:
        """Returns Approve(...) or Skip(rule, ...). copy_percent is the per-target
        override or the global fallback — already resolved by the caller."""
        # 1-2: file flags
        if self.kill_active():
            return Skip(R_KILL_SWITCH, detail="kill switch file present")
        if self.paused():
            return Skip(R_PAUSED, detail="pause file present")

        # 3: SELL with no current long → not in our copying scope
        if trade.side == "SELL" and not persistence.has_open_long(self.conn, trade.asset_token_id):
            return Skip(R_SELL_NO_POSITION,
                        detail="target sold a token we don't hold")

        # 4-5: blacklist / whitelist
        if trade.condition_id in self.risk.blacklist_condition_ids:
            return Skip(R_BLACKLISTED, detail=trade.condition_id)
        if self.risk.whitelist_condition_ids and \
                trade.condition_id not in self.risk.whitelist_condition_ids:
            return Skip(R_NOT_WHITELISTED, detail=trade.condition_id)

        # 6: copy size cap
        target_notional = trade.notional_usdc
        our_notional = round(target_notional * copy_percent, 4)
        capped = False
        if our_notional > self.risk.max_trade_usdc:
            if self.risk.cap_oversize_trades:
                our_notional = self.risk.max_trade_usdc
                capped = True
            else:
                return Skip(R_MAX_TRADE,
                            detail=f"would size ${target_notional * copy_percent:.2f}",
                            observed_value=target_notional * copy_percent,
                            threshold=self.risk.max_trade_usdc)
        if our_notional <= 0:
            return Skip(R_MAX_TRADE, detail="computed notional <= 0",
                        observed_value=our_notional, threshold=0)

        # 7: daily loss circuit breaker
        loss = persistence.get_today_realized_loss(self.conn)
        if loss >= self.risk.max_daily_loss_usdc:
            return Skip(R_DAILY_LOSS,
                        detail=f"today's loss ${loss:.2f}",
                        observed_value=loss,
                        threshold=self.risk.max_daily_loss_usdc)

        # 8: per-market exposure cap
        existing = persistence.get_market_exposure(self.conn, trade.condition_id)
        signed_delta = our_notional if trade.side == "BUY" else -our_notional
        projected = existing + signed_delta
        if abs(projected) > self.risk.max_market_exposure_usdc:
            return Skip(R_MARKET_EXPOSURE,
                        detail=f"projected ${projected:.2f}",
                        observed_value=projected,
                        threshold=self.risk.max_market_exposure_usdc)

        # 9: live snapshot (single network call gates remaining rules)
        snap: Optional[MarketSnapshot] = self.market_data.get_snapshot(trade.condition_id)
        if snap is None:
            return Skip(R_MARKET_DATA_DOWN, detail="snapshot fetch failed")

        # 13: book sanity (cheap to check now that we have the snap)
        if not (0 < snap.best_bid < snap.best_ask < 1):
            return Skip(R_BAD_BOOK,
                        detail=f"bid={snap.best_bid} ask={snap.best_ask}")

        # 10: liquidity floor
        if snap.liquidity_usdc < self.risk.min_market_liquidity_usdc:
            return Skip(R_LIQUIDITY,
                        detail=f"liquidity ${snap.liquidity_usdc:.0f}",
                        observed_value=snap.liquidity_usdc,
                        threshold=self.risk.min_market_liquidity_usdc)

        # 11: price drift since target traded
        drift_pct = abs(snap.mid - trade.price) / trade.price * 100.0
        if drift_pct > self.risk.max_price_move_after_target:
            return Skip(R_PRICE_MOVE,
                        detail=f"drift {drift_pct:.2f}%",
                        observed_value=drift_pct,
                        threshold=self.risk.max_price_move_after_target)

        # 12: slippage at our execution side
        execution_price = snap.best_ask if trade.side == "BUY" else snap.best_bid
        slippage_pct = abs(execution_price - snap.mid) / snap.mid * 100.0
        if slippage_pct > self.risk.max_slippage_percent:
            return Skip(R_SLIPPAGE,
                        detail=f"slippage {slippage_pct:.2f}%",
                        observed_value=slippage_pct,
                        threshold=self.risk.max_slippage_percent)

        our_size = round(our_notional / execution_price, 2)
        if our_size <= 0:
            return Skip(R_MAX_TRADE, detail="rounded size 0",
                        observed_value=our_size, threshold=0)

        return Approve(
            our_size=our_size,
            our_price=execution_price,
            mid=snap.mid,
            slippage_pct=slippage_pct,
        )
