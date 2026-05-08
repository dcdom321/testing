"""Trade normalization + validation."""
import json
from pathlib import Path

import pytest

from copybot.normalize import normalize_trade


FIX = Path(__file__).parent / "fixtures" / "sample_trades.json"


def _good():
    return json.loads(FIX.read_text())[0]


def test_valid_buy():
    t = normalize_trade(_good())
    assert t is not None
    assert t.side == "BUY"
    assert t.size == 100.0
    assert t.price == 0.42
    assert t.notional_usdc == pytest.approx(42.0)
    assert t.target_wallet == "0xabcdef0000000000000000000000000000000001"


def test_lowercases_wallet():
    raw = _good()
    raw["proxyWallet"] = "0xABCDEF0000000000000000000000000000000099"
    t = normalize_trade(raw)
    assert t.target_wallet == "0xabcdef0000000000000000000000000000000099"


def test_side_case_insensitive():
    raw = _good()
    raw["side"] = "buy"
    assert normalize_trade(raw).side == "BUY"


def test_rejects_invalid_side():
    raw = _good(); raw["side"] = "SHORT"
    assert normalize_trade(raw) is None


def test_rejects_zero_size():
    raw = _good(); raw["size"] = 0
    assert normalize_trade(raw) is None


def test_rejects_price_out_of_range():
    raw = _good()
    for bad in (0, -0.1, 1.0, 1.01):
        raw["price"] = bad
        assert normalize_trade(raw) is None, bad


def test_rejects_missing_required():
    for missing in ("transactionHash", "asset", "conditionId", "proxyWallet", "timestamp"):
        raw = _good()
        raw.pop(missing)
        assert normalize_trade(raw) is None, missing


def test_optional_fields_round_trip():
    t = normalize_trade(_good())
    assert t.title == "Will the Mets win the World Series?"
    assert t.outcome == "Yes"
    assert t.outcome_index == 0


def test_handles_garbage_input():
    assert normalize_trade({}) is None
    assert normalize_trade({"side": "BUY", "size": "not a number"}) is None
