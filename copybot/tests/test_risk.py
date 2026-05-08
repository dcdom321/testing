"""Risk-engine tests (spec-mandated). Covers every rule + ordering + boundary."""
import time
from pathlib import Path

import pytest

from copybot import persistence, risk
from copybot.config import Config, RiskCfg
from copybot.models import Approve, CopiedTrade, MarketSnapshot, Skip, Trade


MIGRATIONS_DIR = str(Path(__file__).parent.parent / "migrations")


# ---------- helpers --------------------------------------------------------

class FakeMarketData:
    """Returns canned snapshots; lets a test force a None response."""
    def __init__(self, snap=None):
        self.snap = snap
        self.calls = 0

    def get_snapshot(self, cid):
        self.calls += 1
        if isinstance(self.snap, Exception):
            raise self.snap
        return self.snap


def _conn(tmp_path):
    db = tmp_path / "test.db"
    c = persistence.connect(str(db))
    persistence.run_migrations(c, MIGRATIONS_DIR)
    return c


def _trade(**over):
    base = dict(
        tx_hash="0xa1", target_wallet="0xT", asset_token_id="t1",
        condition_id="c1", side="BUY", size=200.0, price=0.40,
        notional_usdc=80.0, ts=1700000000,
    )
    base.update(over)
    return Trade(**base)


def _snap(**over):
    # bid 0.398 / ask 0.402 / mid 0.40 → slippage 0.5% (well under 2% default)
    base = dict(
        condition_id="c1", best_bid=0.398, best_ask=0.402, mid=0.40,
        liquidity_usdc=5000.0, volume_usdc=50_000.0, fetched_at=int(time.time()),
    )
    base.update(over)
    return MarketSnapshot(**base)


def _cfg(**risk_over):
    rk = RiskCfg(**{**RiskCfg().__dict__, **risk_over,
                    "kill_switch_file": "/tmp/copybot_test_kill",
                    "pause_file":       "/tmp/copybot_test_pause"})
    # rebuild Config with the risk override; rest defaults are fine
    return Config(risk=rk)


_DEFAULT = object()


def _gate(tmp_path, cfg=None, snap=_DEFAULT):
    cfg = cfg or _cfg()
    md = FakeMarketData(_snap() if snap is _DEFAULT else snap)
    g = risk.CopyRiskGate(cfg, _conn(tmp_path), md)
    g.reset_kill_switch(); g.resume()
    return g, md


# ---------- happy path -----------------------------------------------------

def test_approves_with_correct_sizing(tmp_path):
    g, _ = _gate(tmp_path)
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Approve)
    # target notional 80 * 5% = 4 USDC; ask 0.402 → ~9.95 shares
    assert d.our_size == pytest.approx(4.0 / 0.402, rel=0.01)
    assert d.our_price == 0.402
    assert d.mid == 0.40


def test_caps_oversize_trade_when_enabled(tmp_path):
    cfg = _cfg(max_trade_usdc=2.0, cap_oversize_trades=True)
    g, _ = _gate(tmp_path, cfg=cfg)
    d = g.evaluate(_trade(), copy_percent=0.10)  # 80 * 10% = 8 → would exceed cap 2
    assert isinstance(d, Approve)
    # cap is 2 USDC at 0.402 ask → ~4.97 shares
    assert d.our_size * d.our_price == pytest.approx(2.0, rel=0.01)


def test_skips_oversize_trade_when_disabled(tmp_path):
    cfg = _cfg(max_trade_usdc=2.0, cap_oversize_trades=False)
    g, _ = _gate(tmp_path, cfg=cfg)
    d = g.evaluate(_trade(), copy_percent=0.10)
    assert isinstance(d, Skip) and d.rule == risk.R_MAX_TRADE


# ---------- file flags ----------------------------------------------------

def test_kill_switch_short_circuits_before_market_fetch(tmp_path):
    g, md = _gate(tmp_path)
    g.trip_kill_switch("test")
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_KILL_SWITCH
    assert md.calls == 0


def test_pause_short_circuits(tmp_path):
    g, md = _gate(tmp_path)
    g.pause("ops")
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_PAUSED
    assert md.calls == 0


# ---------- list filters --------------------------------------------------

def test_blacklist_skips(tmp_path):
    cfg = _cfg(blacklist_condition_ids=("c1",))
    g, _ = _gate(tmp_path, cfg=cfg)
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_BLACKLISTED


def test_whitelist_miss_skips(tmp_path):
    cfg = _cfg(whitelist_condition_ids=("c2",))
    g, _ = _gate(tmp_path, cfg=cfg)
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_NOT_WHITELISTED


def test_whitelist_hit_passes(tmp_path):
    cfg = _cfg(whitelist_condition_ids=("c1",))
    g, _ = _gate(tmp_path, cfg=cfg)
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Approve)


# ---------- DB-backed rules -----------------------------------------------

def test_skips_sell_when_no_position(tmp_path):
    g, _ = _gate(tmp_path)
    d = g.evaluate(_trade(side="SELL"), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_SELL_NO_POSITION


def test_daily_loss_breaker(tmp_path):
    cfg = _cfg(max_daily_loss_usdc=10.0)
    g, _ = _gate(tmp_path, cfg=cfg)
    # seed a target trade + a closed losing copy
    tid = persistence.insert_target_trade(g.conn, _trade(tx_hash="0xseed"), "{}")
    now = int(time.time())
    persistence.insert_copied_trade(g.conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.0,
        copy_ratio=0.05, client_order_id="L1", status="paper",
        submitted_at=now, filled_at=now, realized_pnl_usdc=-15.0,
    ))
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_DAILY_LOSS


def test_market_exposure_cap(tmp_path):
    cfg = _cfg(max_market_exposure_usdc=5.0)
    g, _ = _gate(tmp_path, cfg=cfg)
    tid = persistence.insert_target_trade(g.conn, _trade(tx_hash="0xseed"), "{}")
    now = int(time.time())
    persistence.insert_copied_trade(g.conn, CopiedTrade(
        target_trade_id=tid, mode="paper", side="BUY", asset_token_id="t1",
        condition_id="c1", our_size=10, our_price=0.4, our_notional_usdc=4.5,
        copy_ratio=0.05, client_order_id="E1", status="paper",
        submitted_at=now, filled_size=10,
    ))
    # next BUY of 4 USDC would push exposure to 8.5 > cap 5
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_MARKET_EXPOSURE


# ---------- snapshot-derived rules -----------------------------------------

def test_market_data_unavailable(tmp_path):
    g, _ = _gate(tmp_path, snap=None)
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_MARKET_DATA_DOWN


def test_liquidity_floor(tmp_path):
    g, _ = _gate(tmp_path, snap=_snap(liquidity_usdc=100.0))
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_LIQUIDITY


def test_price_drift_skip(tmp_path):
    cfg = _cfg(max_price_move_after_target=1.0)  # very tight
    # target priced 0.40, current mid 0.43 → ~7.5% drift
    g, _ = _gate(tmp_path, cfg=cfg, snap=_snap(best_bid=0.42, best_ask=0.44, mid=0.43))
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_PRICE_MOVE


def test_slippage_skip(tmp_path):
    cfg = _cfg(max_slippage_percent=0.5)
    g, _ = _gate(tmp_path, cfg=cfg, snap=_snap(best_bid=0.30, best_ask=0.50, mid=0.40))
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_SLIPPAGE


def test_invalid_book_skip(tmp_path):
    g, _ = _gate(tmp_path, snap=_snap(best_bid=0.5, best_ask=0.4, mid=0.45))
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert isinstance(d, Skip) and d.rule == risk.R_BAD_BOOK


# ---------- ordering invariants -------------------------------------------

def test_kill_switch_beats_whitelist(tmp_path):
    cfg = _cfg(whitelist_condition_ids=("c2",))
    g, md = _gate(tmp_path, cfg=cfg)
    g.trip_kill_switch()
    d = g.evaluate(_trade(), copy_percent=0.05)
    assert d.rule == risk.R_KILL_SWITCH
    assert md.calls == 0  # never even touched market data


def test_local_rules_run_before_market_fetch(tmp_path):
    cfg = _cfg(blacklist_condition_ids=("c1",))
    g, md = _gate(tmp_path, cfg=cfg)
    g.evaluate(_trade(), copy_percent=0.05)
    assert md.calls == 0
