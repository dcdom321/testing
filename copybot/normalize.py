"""Convert raw data-api `/trades` JSON into a typed Trade. Reject malformed."""
from __future__ import annotations

from typing import Optional

from .models import Trade


def normalize_trade(raw: dict) -> Optional[Trade]:
    """Validate and convert one raw trade dict. Returns None on malformed input."""
    try:
        side = str(raw.get("side", "")).strip().upper()
        if side not in ("BUY", "SELL"):
            return None

        size = float(raw.get("size") or 0)
        price = float(raw.get("price") or 0)
        if size <= 0 or not (0 < price < 1):
            return None

        tx_hash = str(raw.get("transactionHash") or "").strip()
        token_id = str(raw.get("asset") or "").strip()
        condition_id = str(raw.get("conditionId") or "").strip()
        wallet = str(raw.get("proxyWallet") or "").strip().lower()
        if not (tx_hash and token_id and condition_id and wallet):
            return None

        ts = int(raw.get("timestamp") or 0)
        if ts <= 0:
            return None

        outcome_idx = raw.get("outcomeIndex")
        outcome_idx = int(outcome_idx) if outcome_idx is not None else None

        return Trade(
            tx_hash=tx_hash,
            target_wallet=wallet,
            asset_token_id=token_id,
            condition_id=condition_id,
            side=side,
            size=size,
            price=price,
            notional_usdc=round(size * price, 6),
            ts=ts,
            outcome=raw.get("outcome"),
            outcome_index=outcome_idx,
            title=raw.get("title"),
            slug=raw.get("slug"),
            event_slug=raw.get("eventSlug"),
        )
    except (TypeError, ValueError):
        return None
