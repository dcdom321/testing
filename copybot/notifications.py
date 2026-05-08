"""Best-effort outbound alerts. Discord webhook + Telegram bot. Both optional.

Silent if creds aren't set. Wraps every send in try/except — never raises so
it can't take the trading loop down.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from .config import Config, NotificationsCfg
from .models import CopiedTrade, SkipReason, Trade


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        n: NotificationsCfg = cfg.notifications
        self.discord_url = os.environ.get(n.discord_webhook_env, "").strip()
        self.tg_token = os.environ.get(n.telegram_token_env, "").strip()
        self.tg_chat = os.environ.get(n.telegram_chat_env, "").strip()
        self.session = requests.Session()

    def _enabled_discord(self) -> bool:
        return bool(self.discord_url)

    def _enabled_tg(self) -> bool:
        return bool(self.tg_token and self.tg_chat)

    # -- public events ------------------------------------------------------
    def target_trade(self, trade: Trade):
        if not self.cfg.notifications.notify_on_target:
            return
        msg = (f"[TARGET] {trade.target_wallet[:10]}.. "
               f"{trade.side} {trade.size:.0f} @ ${trade.price:.3f} "
               f"({trade.notional_usdc:.2f} USDC)\n{trade.title or trade.condition_id}")
        self._send(msg)

    def trade_copied(self, ct: CopiedTrade):
        if not self.cfg.notifications.notify_on_copy:
            return
        tag = "PAPER" if ct.mode == "paper" else "LIVE"
        msg = (f"[COPY {tag}] {ct.side} {ct.our_size:.2f} @ ${ct.our_price:.3f} "
               f"= {ct.our_notional_usdc:.2f} USDC ({ct.status})")
        self._send(msg)

    def trade_skipped(self, reason: SkipReason, trade: Trade):
        if not self.cfg.notifications.notify_on_skip:
            return
        msg = (f"[SKIP] {reason.rule}: {reason.detail} "
               f"(target {trade.side} ${trade.notional_usdc:.2f} on "
               f"{trade.condition_id[:10]}..)")
        self._send(msg)

    def risk_hit(self, rule: str, detail: str):
        if not self.cfg.notifications.notify_on_risk_hit:
            return
        self._send(f"[RISK] {rule}: {detail}")

    def error(self, source: str, msg: str):
        if not self.cfg.notifications.notify_on_error:
            return
        self._send(f"[ERROR] {source}: {msg}")

    # -- transports ---------------------------------------------------------
    def _send(self, text: str):
        if self._enabled_discord():
            try:
                self.session.post(self.discord_url, json={"content": text}, timeout=5)
            except Exception:
                pass  # best effort; do not break the trading loop
        if self._enabled_tg():
            try:
                self.session.post(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    json={"chat_id": self.tg_chat, "text": text},
                    timeout=5,
                )
            except Exception:
                pass
