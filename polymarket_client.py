"""Polymarket execution wrapper.

Real-money order placement via py-clob-client. Construct via `from_config(cfg)`;
falls back to a no-op stub when `live_trading` is false so the rest of the bot
can call this unconditionally.

Wallet credentials are read from config first, then env vars (env wins). Storing
a raw private key in config.json works but `*_env` lookup is preferred.
"""
import os
import time
from typing import Optional

POLYGON_CHAIN_ID = 137
DEFAULT_HOST = "https://clob.polymarket.com"


class _StubClient:
    """No-op client used when live trading is disabled. Every call returns a
    dry-run dict so callers can log and continue without a try/except wall."""
    live = False

    def place_buy(self, token_id, price, size, client_order_id=None):
        return {"status": "dry_run", "side": "BUY", "token_id": token_id,
                "price": price, "size": size, "client_order_id": client_order_id}

    def place_sell(self, token_id, price, size, client_order_id=None):
        return {"status": "dry_run", "side": "SELL", "token_id": token_id,
                "price": price, "size": size, "client_order_id": client_order_id}

    def cancel(self, order_id):
        return {"status": "dry_run_cancel", "order_id": order_id}

    def get_order(self, order_id):
        return {"status": "dry_run", "order_id": order_id}

    def get_open_orders(self):
        return []

    def reconcile(self, local_positions):
        return {"checked": 0, "drift": []}


class PolymarketClient:
    """Wraps py-clob-client. Lazy import so paper-mode users don't need the dep."""
    live = True

    def __init__(self, host, private_key, funder, signature_type, chain_id):
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams
        from py_clob_client.order_builder.constants import BUY, SELL

        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._OpenOrderParams = OpenOrderParams
        self._BUY = BUY
        self._SELL = SELL

        self._client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=signature_type,
            funder=funder,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    def _post(self, side, token_id, price, size, client_order_id):
        args = self._OrderArgs(
            token_id=token_id,
            price=round(float(price), 3),
            size=round(float(size), 2),
            side=side,
        )
        signed = self._client.create_order(args)
        resp = self._client.post_order(signed, self._OrderType.GTC)
        if isinstance(resp, dict):
            resp.setdefault("client_order_id", client_order_id)
            resp.setdefault("submitted_at", time.time())
        return resp

    def place_buy(self, token_id, price, size, client_order_id=None):
        return self._post(self._BUY, token_id, price, size, client_order_id)

    def place_sell(self, token_id, price, size, client_order_id=None):
        return self._post(self._SELL, token_id, price, size, client_order_id)

    def cancel(self, order_id):
        return self._client.cancel(order_id=order_id)

    def get_order(self, order_id):
        return self._client.get_order(order_id)

    def get_open_orders(self):
        return self._client.get_orders(self._OpenOrderParams())

    def reconcile(self, local_positions):
        """Compare local open orders against Polymarket's view. Returns a
        report; callers decide what to repair. Read-only — no mutations."""
        try:
            remote = {o["id"]: o for o in self.get_open_orders() or []}
        except Exception as e:
            return {"checked": 0, "drift": [], "error": str(e)}

        drift = []
        for pos in local_positions:
            oid = pos.get("order_id")
            if not oid:
                continue
            if oid not in remote:
                drift.append({"order_id": oid, "issue": "missing_remote",
                              "market_id": pos.get("market_id")})
        return {"checked": len(local_positions), "drift": drift}


def _load_key(wallet_cfg):
    env_var = wallet_cfg.get("private_key_env")
    if env_var:
        v = os.environ.get(env_var, "").strip()
        if v:
            return v
    return wallet_cfg.get("private_key", "").strip()


def _load_funder(wallet_cfg):
    env_var = wallet_cfg.get("funder_address_env")
    if env_var:
        v = os.environ.get(env_var, "").strip()
        if v:
            return v
    return wallet_cfg.get("funder_address", "").strip()


def from_config(cfg) -> object:
    """Return a live PolymarketClient or a _StubClient depending on config.
    Stub is returned (with a printed reason) on any init failure — never raises
    so paper mode keeps working even with a broken wallet config."""
    if not cfg.get("live_trading", False):
        return _StubClient()

    wallet = cfg.get("wallet", {})
    pk = _load_key(wallet)
    funder = _load_funder(wallet)
    if not pk:
        print("  [WALLET] live_trading=true but no private key configured; using stub")
        return _StubClient()
    if not funder:
        print("  [WALLET] live_trading=true but no funder_address configured; using stub")
        return _StubClient()

    host = wallet.get("host", DEFAULT_HOST)
    sig_type = int(wallet.get("signature_type", 2))
    chain_id = int(wallet.get("chain_id", POLYGON_CHAIN_ID))

    try:
        client = PolymarketClient(host, pk, funder, sig_type, chain_id)
        print(f"  [WALLET] live trading enabled (funder={funder[:6]}...{funder[-4:]}, sig_type={sig_type})")
        return client
    except Exception as e:
        print(f"  [WALLET] init failed ({e}); using stub")
        return _StubClient()
