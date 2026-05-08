"""Poll Polymarket data-api `/trades?user=<addr>` per target wallet.

Returns raw dicts in chronological order (oldest first) so the caller can
process them in the same order the target executed them.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

import requests


DEFAULT_PAGE_LIMIT = 100  # Polymarket allows up to 500


class WalletPoller:
    """One poller per target wallet. Holds a session for keep-alive."""

    def __init__(self, base_url: str, wallet: str,
                 session: Optional[requests.Session] = None,
                 page_limit: int = DEFAULT_PAGE_LIMIT):
        self.base_url = base_url.rstrip("/")
        self.wallet = wallet.lower()
        self.session = session or requests.Session()
        self.page_limit = page_limit

    def poll(self, since_ts: Optional[int]) -> list:
        """Return raw trades with `timestamp > since_ts` (or all if None),
        sorted ascending by timestamp. Paginates by `offset` while pages are
        full and the oldest entry is still newer than the cursor."""
        out: list = []
        offset = 0
        while True:
            params = {
                "user":   self.wallet,
                "limit":  self.page_limit,
                "offset": offset,
            }
            try:
                r = self.session.get(
                    f"{self.base_url}/trades", params=params, timeout=(5, 10)
                )
                page = r.json()
            except Exception as e:
                print(f"  [INGEST] {self.wallet[:8]}.. fetch failed: {e}")
                break
            if not isinstance(page, list) or not page:
                break

            kept = []
            for t in page:
                ts = int(t.get("timestamp") or 0)
                if since_ts is not None and ts <= since_ts:
                    continue
                kept.append(t)
            out.extend(kept)

            # Stop paginating once we hit a page whose oldest trade is
            # already at or before the cursor — older pages can't contain new.
            if since_ts is not None and len(kept) < len(page):
                break
            if len(page) < self.page_limit:
                break
            offset += self.page_limit
            if offset >= 500:  # hard stop; data-api is meant for recent activity
                break

        out.sort(key=lambda t: int(t.get("timestamp") or 0))
        return out
