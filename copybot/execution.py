"""Real-mode execution. Reuses the parent repo's polymarket_client.

The parent's `from_config()` returns a stub when `live_trading` is false or
wallet credentials are missing — that means the bot never accidentally trades
real money even if a config flag is wrong.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from . import persistence
from .config import Config, parent_polymarket_client_cfg
from .models import Approve, CopiedTrade, Trade


# Allow `import polymarket_client` from the parent repo regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import polymarket_client  # noqa: E402


def build_client(cfg: Config):
    """Returns a live PolymarketClient or a stub. Never raises."""
    return polymarket_client.from_config(parent_polymarket_client_cfg(cfg))


class ExecutionEngine:
    def __init__(self, conn: sqlite3.Connection, client):
        self.conn = conn
        self.client = client

    def execute(self, target_trade_id: int, trade: Trade,
                decision: Approve, copy_percent: float,
                client_order_id: str) -> CopiedTrade:
        now = int(time.time())

        try:
            if trade.side == "BUY":
                resp = self.client.place_buy(
                    token_id=trade.asset_token_id,
                    price=decision.our_price,
                    size=decision.our_size,
                    client_order_id=client_order_id,
                )
            else:
                resp = self.client.place_sell(
                    token_id=trade.asset_token_id,
                    price=decision.our_price,
                    size=decision.our_size,
                    client_order_id=client_order_id,
                )
        except Exception as e:
            persistence.insert_error(self.conn, "execution", str(e),
                                     {"client_order_id": client_order_id})
            return self._record(target_trade_id, trade, decision, copy_percent,
                                client_order_id, status="rejected",
                                exchange_id=None, raw=str(e), now=now,
                                filled=0.0, fill_price=None)

        # Stub returns dry_run; live response varies. Treat success liberally.
        if not isinstance(resp, dict):
            resp = {"raw": str(resp)}
        success = resp.get("success", True) and resp.get("status") not in ("error", "rejected")
        exchange_id = resp.get("orderID") or resp.get("id")
        status = "pending" if success else "rejected"
        # Optimistic: mark filled if the response says so.
        if success and resp.get("status") in ("matched", "filled"):
            status = "filled"

        return self._record(target_trade_id, trade, decision, copy_percent,
                            client_order_id, status=status,
                            exchange_id=exchange_id,
                            raw=json.dumps(resp, default=str), now=now,
                            filled=decision.our_size if status == "filled" else 0.0,
                            fill_price=decision.our_price if status == "filled" else None)

    def _record(self, target_trade_id, trade, decision, copy_percent,
                client_order_id, *, status, exchange_id, raw, now,
                filled, fill_price) -> CopiedTrade:
        ct = CopiedTrade(
            target_trade_id=target_trade_id,
            mode="real",
            side=trade.side,
            asset_token_id=trade.asset_token_id,
            condition_id=trade.condition_id,
            our_size=decision.our_size,
            our_price=decision.our_price,
            our_notional_usdc=round(decision.our_size * decision.our_price, 4),
            copy_ratio=copy_percent,
            client_order_id=client_order_id,
            exchange_order_id=exchange_id,
            status=status,
            filled_size=filled,
            filled_avg_price=fill_price,
            submitted_at=now,
            filled_at=now if status == "filled" else None,
            raw_response=raw,
        )
        persistence.insert_copied_trade(self.conn, ct)
        return ct
