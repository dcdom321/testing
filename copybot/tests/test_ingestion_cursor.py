"""Cursor + pagination behavior."""
from unittest.mock import MagicMock

from copybot.ingestion import WalletPoller


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


def _fake_session(pages):
    s = MagicMock()
    s.get.side_effect = [_resp(p) for p in pages]
    return s


def test_first_poll_returns_all_when_no_cursor():
    page = [{"timestamp": 100}, {"timestamp": 200}, {"timestamp": 300}]
    s = _fake_session([page])
    p = WalletPoller("http://x", "0xA", session=s, page_limit=100)
    out = p.poll(since_ts=None)
    assert [t["timestamp"] for t in out] == [100, 200, 300]


def test_filters_by_cursor():
    page = [{"timestamp": 100}, {"timestamp": 200}, {"timestamp": 300}]
    s = _fake_session([page])
    p = WalletPoller("http://x", "0xA", session=s, page_limit=100)
    out = p.poll(since_ts=200)
    assert [t["timestamp"] for t in out] == [300]


def test_stops_paginating_when_short_page():
    short = [{"timestamp": i} for i in (10, 20, 30)]
    s = _fake_session([short])
    p = WalletPoller("http://x", "0xA", session=s, page_limit=100)
    p.poll(since_ts=None)
    assert s.get.call_count == 1


def test_paginates_when_full_page_and_all_new():
    page1 = [{"timestamp": i} for i in range(100, 200)]   # 100 trades, all new
    page2 = [{"timestamp": i} for i in range(50, 100)]
    s = _fake_session([page1, page2])
    p = WalletPoller("http://x", "0xA", session=s, page_limit=100)
    out = p.poll(since_ts=None)
    assert len(out) == 150
    assert s.get.call_count == 2
    # results sorted ascending
    assert out[0]["timestamp"] < out[-1]["timestamp"]


def test_stops_paginating_when_cursor_inside_page():
    page1 = [{"timestamp": i} for i in range(100, 200)]   # full page, all new
    page2 = [{"timestamp": i} for i in range(50, 100)]    # all <= cursor 60
    s = _fake_session([page1, page2])
    p = WalletPoller("http://x", "0xA", session=s, page_limit=100)
    out = p.poll(since_ts=60)
    assert s.get.call_count == 2
    # second page returned 0 new → loop exits
    assert all(t["timestamp"] > 60 for t in out)


def test_lowercases_wallet_in_request():
    s = _fake_session([[]])
    p = WalletPoller("http://x", "0xABCDEF", session=s)
    p.poll(since_ts=None)
    args, kwargs = s.get.call_args
    assert kwargs["params"]["user"] == "0xabcdef"
