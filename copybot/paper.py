"""Paper-trading engine. Default mode.

Simulates a fill at the *current* execution-side price (best_ask for BUY,
best_bid for SELL) so paper PnL reflects realistic slippage.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from . import persistence
from .models import Approve, CopiedTrade, Trade


class PaperEngine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, target_trade_id: int, trade: Trade,
                decision: Approve, copy_percent: float,
                client_order_id: str) -> CopiedTrade:
        """Record a paper fill against the snapshot the risk engine used."""
        now = int(time.time())
        ct = CopiedTrade(
            target_trade_id=target_trade_id,
            mode="paper",
            side=trade.side,
            asset_token_id=trade.asset_token_id,
            condition_id=trade.condition_id,
            our_size=decision.our_size,
            our_price=decision.our_price,
            our_notional_usdc=round(decision.our_size * decision.our_price, 4),
            copy_ratio=copy_percent,
            client_order_id=client_order_id,
            status="paper",
            filled_size=decision.our_size,
            filled_avg_price=decision.our_price,
            submitted_at=now,
            filled_at=now,
            raw_response=None,
        )
        persistence.insert_copied_trade(self.conn, ct)
        return ct
