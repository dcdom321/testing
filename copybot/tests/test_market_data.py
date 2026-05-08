"""GammaProvider parser + TTL cache + error handling."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from copybot.market_data import GammaProvider, _parse_gamma


FIX = Path(__file__).parent / "fixtures" / "sample_market.json"


def _fake_session(payload, side_effect=None):
    s = MagicMock()
    if side_effect:
        s.get.side_effect = side_effect
        return s
    resp = MagicMock()
    resp.json.return_value = payload
    s.get.return_value = resp
    return s


def test_parses_real_fixture():
    raw = json.loads(FIX.read_text())
    snap = _parse_gamma(raw, fetched_at=1000)
    assert snap.best_bid == 0.41
    assert snap.best_ask == 0.43
    assert snap.mid == pytest.approx(0.42)
    assert snap.liquidity_usdc == pytest.approx(12500.5)


def test_falls_back_to_outcome_prices():
    raw = json.loads(FIX.read_text())
    raw["bestBid"] = 0
    raw["bestAsk"] = 0
    snap = _parse_gamma(raw, fetched_at=1000)
    assert snap is not None
    assert snap.best_bid == 0.41
    assert snap.best_ask == 0.43


def test_rejects_invalid_book():
    raw = json.loads(FIX.read_text())
    raw["bestBid"] = 0; raw["bestAsk"] = 0; raw["outcomePrices"] = "[]"
    assert _parse_gamma(raw, fetched_at=1000) is None


def test_cache_hits_within_ttl():
    payload = json.loads(FIX.read_text())
    sess = _fake_session(payload)
    p = GammaProvider(cache_ttl_seconds=10, session=sess)
    a = p.get_snapshot("c1"); b = p.get_snapshot("c1")
    assert a is b
    assert sess.get.call_count == 1


def test_cache_expires(monkeypatch):
    payload = json.loads(FIX.read_text())
    sess = _fake_session(payload)
    p = GammaProvider(cache_ttl_seconds=1, session=sess)
    p.get_snapshot("c1")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 5)
    p.get_snapshot("c1")
    assert sess.get.call_count == 2


def test_http_error_returns_none_no_raise():
    sess = _fake_session(None, side_effect=Exception("boom"))
    p = GammaProvider(session=sess)
    assert p.get_snapshot("c1") is None
