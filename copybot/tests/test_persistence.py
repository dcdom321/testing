"""Migrations + uniqueness + transaction tests."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from copybot import persistence
from copybot.models import CopiedTrade, SkipReason, Trade


MIGRATIONS_DIR = str(Path(__file__).parent.parent / "migrations")


def _conn(tmp_path):
    db = tmp_path / "test.db"
    return persistence.connect(str(db))


def _trade(tx="0xa1", wallet="0xT", token="t1", cid="c1") -> Trade:
    return Trade(
        tx_hash=tx, target_wallet=wallet, asset_token_id=token, condition_id=cid,
        side="BUY", size=10.0, price=0.4, notional_usdc=4.0, ts=1700000000,
    )


def test_migrations_idempotent(tmp_path):
    conn = _conn(tmp_path)
    a = persistence.run_migrations(conn, MIGRATIONS_DIR)
    b = persistence.run_migrations(conn, MIGRATIONS_DIR)
    assert len(a) == 1 and b == [], "second run should apply nothing"
    versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version")]
    assert versions == [1]


def test_target_trade_unique_tx_hash(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    t = _trade()
    id1 = persistence.insert_target_trade(conn, t, raw_json="{}")
    id2 = persistence.insert_target_trade(conn, t, raw_json="{}")
    assert id1 == id2, "duplicate tx_hash should return same row id"


def test_transaction_rolls_back_on_raise(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    with pytest.raises(RuntimeError):
        with persistence.transaction(conn):
            persistence.insert_target_trade(conn, _trade("0xff"), "{}")
            raise RuntimeError("boom")
    n = conn.execute("SELECT COUNT(*) c FROM target_trades").fetchone()["c"]
    assert n == 0


def test_market_exposure_aggregation(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    tid = persistence.insert_target_trade(conn, _trade("0x1"), "{}")
    persistence.insert_copied_trade(conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.0,
        copy_ratio=0.05, client_order_id="cob-1", status="paper",
        submitted_at=int(time.time()), filled_size=10,
    ))
    persistence.insert_copied_trade(conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=5, our_price=0.5, our_notional_usdc=2.5,
        copy_ratio=0.05, client_order_id="cob-2", status="paper",
        submitted_at=int(time.time()), filled_size=5,
    ))
    assert persistence.get_market_exposure(conn, "c1") == pytest.approx(6.5)


def test_wallet_cursor_upsert(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    assert persistence.get_wallet_cursor(conn, "0xA") is None
    persistence.update_wallet_cursor(conn, "0xA", 1700000000)
    assert persistence.get_wallet_cursor(conn, "0xA") == 1700000000
    persistence.update_wallet_cursor(conn, "0xA", 1700001000, error="x")
    assert persistence.get_wallet_cursor(conn, "0xA") == 1700001000


def test_today_realized_loss_only_counts_negative_today(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    tid = persistence.insert_target_trade(conn, _trade("0x1"), "{}")
    now = int(time.time())
    # negative PnL today
    persistence.insert_copied_trade(conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.0,
        copy_ratio=0.05, client_order_id="L1", status="paper",
        submitted_at=now, filled_at=now, realized_pnl_usdc=-3.0,
    ))
    # positive PnL today (must not affect loss)
    persistence.insert_copied_trade(conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.0,
        copy_ratio=0.05, client_order_id="W1", status="paper",
        submitted_at=now, filled_at=now, realized_pnl_usdc=2.0,
    ))
    # negative PnL from yesterday (must be ignored)
    persistence.insert_copied_trade(conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.0,
        copy_ratio=0.05, client_order_id="OLD", status="paper",
        submitted_at=now - 90000, filled_at=now - 90000, realized_pnl_usdc=-99.0,
    ))
    assert persistence.get_today_realized_loss(conn) == pytest.approx(3.0)


def test_skip_reason_insert_and_query(tmp_path):
    conn = _conn(tmp_path)
    persistence.run_migrations(conn, MIGRATIONS_DIR)
    tid = persistence.insert_target_trade(conn, _trade("0x1"), "{}")
    persistence.insert_skipped_trade(conn, SkipReason(
        target_trade_id=tid, rule="min_liquidity", detail="thin",
        ts=int(time.time()), observed_value=100, threshold=1000,
    ))
    n = conn.execute("SELECT COUNT(*) c FROM skipped_trades WHERE rule='min_liquidity'").fetchone()["c"]
    assert n == 1
