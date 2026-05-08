"""PaperEngine: BUY fills at ask, SELL at bid, persists with status=paper."""
from pathlib import Path

import pytest

from copybot import persistence
from copybot.models import Approve, Trade
from copybot.paper import PaperEngine


MIGRATIONS_DIR = str(Path(__file__).parent.parent / "migrations")


def _conn(tmp_path):
    c = persistence.connect(str(tmp_path / "p.db"))
    persistence.run_migrations(c, MIGRATIONS_DIR)
    return c


def _trade(side="BUY"):
    return Trade(
        tx_hash="0x1", target_wallet="0xT", asset_token_id="t1",
        condition_id="c1", side=side, size=200, price=0.4,
        notional_usdc=80.0, ts=1700000000,
    )


def test_buy_fills_at_decision_price(tmp_path):
    conn = _conn(tmp_path)
    tid = persistence.insert_target_trade(conn, _trade("BUY"), "{}")
    dec = Approve(our_size=10.0, our_price=0.41, mid=0.40, slippage_pct=2.5)
    ct = PaperEngine(conn).execute(tid, _trade("BUY"), dec, 0.05, "co-1")
    assert ct.status == "paper"
    assert ct.filled_avg_price == 0.41
    assert ct.our_notional_usdc == pytest.approx(4.10)


def test_sell_records_correctly(tmp_path):
    conn = _conn(tmp_path)
    tid = persistence.insert_target_trade(conn, _trade("SELL"), "{}")
    dec = Approve(our_size=5.0, our_price=0.39, mid=0.40, slippage_pct=2.5)
    ct = PaperEngine(conn).execute(tid, _trade("SELL"), dec, 0.05, "co-2")
    assert ct.side == "SELL"
    assert ct.filled_avg_price == 0.39


def test_persists_to_db(tmp_path):
    conn = _conn(tmp_path)
    tid = persistence.insert_target_trade(conn, _trade("BUY"), "{}")
    dec = Approve(our_size=10.0, our_price=0.41, mid=0.40, slippage_pct=2.5)
    PaperEngine(conn).execute(tid, _trade("BUY"), dec, 0.05, "co-3")
    rows = list(conn.execute("SELECT mode, status, client_order_id FROM copied_trades"))
    assert len(rows) == 1
    assert rows[0]["mode"] == "paper"
    assert rows[0]["status"] == "paper"
    assert rows[0]["client_order_id"] == "co-3"
