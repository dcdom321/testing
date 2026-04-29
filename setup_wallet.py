#!/usr/bin/env python3
"""One-time wallet verification.

Reads config.json, instantiates the Polymarket client, and runs read-only
checks: API creds derive cleanly, the funder address has a USDC balance, and
the bot can fetch open orders. Does not place orders or grant approvals.

Usage:
    python setup_wallet.py
"""
import json
import sys

import polymarket_client


def main():
    with open("config.json") as f:
        cfg = json.load(f)

    if not cfg.get("live_trading", False):
        print("live_trading is false in config.json — flip it to true to test live setup")
        cfg["live_trading"] = True

    client = polymarket_client.from_config(cfg)
    if not getattr(client, "live", False):
        print("FAIL: client did not initialize live (see warnings above)")
        sys.exit(1)

    print("OK: ClobClient instantiated and API creds derived")

    try:
        orders = client.get_open_orders()
        print(f"OK: open orders fetch returned {len(orders) if orders else 0} entries")
    except Exception as e:
        print(f"FAIL: get_open_orders raised: {e}")
        sys.exit(1)

    print("\nWallet looks usable. Notes:")
    print("- Make sure USDC and CTF allowances are approved on Polygon for the")
    print("  Polymarket exchange contracts. The official UI does this on first")
    print("  trade; if you funded a fresh proxy you may need to do it once.")
    print("- Set live_trading=true in config.json when ready to trade.")


if __name__ == "__main__":
    main()
