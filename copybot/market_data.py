"""Live market snapshots used by the risk engine.

V1 uses Gamma (`/markets/{condition_id}`) — same source as weatherbet. Wraps
in a Protocol so a CLOB-book provider can drop in later.
"""
from __future__ import annotations

import json
import time
from typing import Optional, Protocol

import requests

from .models import MarketSnapshot


class MarketDataProvider(Protocol):
    def get_snapshot(self, condition_id: str) -> Optional[MarketSnapshot]: ...


class GammaProvider:
    """Reads bestBid/bestAsk + a liquidity proxy from gamma-api."""

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com",
                 cache_ttl_seconds: int = 3,
                 session: Optional[requests.Session] = None):
        self._base = base_url.rstrip("/")
        self._ttl = max(0, int(cache_ttl_seconds))
        self._session = session or requests.Session()
        self._cache: dict = {}

    def get_snapshot(self, condition_id: str) -> Optional[MarketSnapshot]:
        now = int(time.time())
        cached = self._cache.get(condition_id)
        if cached and (now - cached.fetched_at) <= self._ttl:
            return cached
        try:
            r = self._session.get(
                f"{self._base}/markets/{condition_id}", timeout=(3, 5)
            )
            data = r.json()
        except Exception as e:
            print(f"  [MARKET] {condition_id} fetch failed: {e}")
            return None

        snap = _parse_gamma(data, now)
        if snap:
            self._cache[condition_id] = snap
        return snap


def _parse_gamma(data: dict, fetched_at: int) -> Optional[MarketSnapshot]:
    if not isinstance(data, dict):
        return None
    cid = str(data.get("conditionId") or data.get("id") or "")
    if not cid:
        return None
    try:
        best_bid = float(data.get("bestBid", 0) or 0)
        best_ask = float(data.get("bestAsk", 0) or 0)
    except (TypeError, ValueError):
        return None
    if best_bid <= 0 and best_ask <= 0:
        # Try outcomePrices as a fallback.
        try:
            prices = json.loads(data.get("outcomePrices") or "[]")
            if prices:
                best_bid = float(prices[0])
                best_ask = float(prices[1]) if len(prices) > 1 else best_bid
        except (TypeError, ValueError):
            return None
    if best_bid <= 0 or best_ask <= 0 or best_ask >= 1:
        return None
    mid = (best_bid + best_ask) / 2

    liquidity = _coerce_float(data, ["liquidityNum", "liquidity"])
    volume = _coerce_float(data, ["volumeNum", "volume"])

    return MarketSnapshot(
        condition_id=cid,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        liquidity_usdc=liquidity,
        volume_usdc=volume,
        fetched_at=fetched_at,
    )


def _coerce_float(d: dict, keys) -> float:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0
