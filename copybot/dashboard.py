"""CLI status renderer + dry-run report."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional


def _ts(t: Optional[int]) -> str:
    if not t:
        return "-"
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def print_status(conn: sqlite3.Connection, risk_gate) -> None:
    snap = risk_gate.snapshot()
    cur = conn.execute("""
        SELECT wallet, last_seen_ts, last_polled_at, last_error
        FROM wallet_cursors
    """).fetchall()
    open_count = conn.execute("""
        SELECT COUNT(*) c FROM copied_trades
        WHERE status IN ('paper','pending','partial','filled')
          AND realized_pnl_usdc IS NULL
    """).fetchone()["c"]

    today_start = int(time.time()) - (int(time.time()) % 86400)
    pnl_row = conn.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN realized_pnl_usdc IS NOT NULL THEN realized_pnl_usdc ELSE 0 END), 0) AS realized,
          COUNT(*) AS copies
        FROM copied_trades
        WHERE submitted_at >= ?
    """, (today_start,)).fetchone()

    print()
    print("  COPYBOT STATUS")
    print("  " + "-" * 50)
    print(f"  kill_switch:  {snap['kill_switch']}")
    print(f"  paused:       {snap['paused']}")
    print(f"  copy_pct:     {snap['copy_trade_percent']*100:.2f}%")
    print(f"  caps:         per-trade ${snap['max_trade_usdc']} | "
          f"daily-loss ${snap['max_daily_loss_usdc']} | "
          f"market ${snap['max_market_exposure_usdc']}")
    print(f"  open trades:  {open_count}")
    print(f"  today copies: {pnl_row['copies']}  realized PnL: ${pnl_row['realized']:.2f}")
    print()
    print("  Wallets:")
    if not cur:
        print("    (none polled yet)")
    for row in cur:
        err = f"  err={row['last_error']}" if row["last_error"] else ""
        print(f"    {row['wallet']}  last_trade={_ts(row['last_seen_ts'])}"
              f"  polled={_ts(row['last_polled_at'])}{err}")
    print()


def dry_run_report(conn: sqlite3.Connection, hours: int = 24) -> None:
    """What would the bot have done over the last N hours, given the trades
    it actually observed? In paper mode the trades it *did* take show up; in
    real mode the trades it would have if real-mode were on. Either way, the
    skipped_trades table tells you what was filtered and why."""
    cutoff = int(time.time()) - hours * 3600

    targets = conn.execute(
        "SELECT COUNT(*) c FROM target_trades WHERE ts >= ?", (cutoff,)
    ).fetchone()["c"]

    copies = conn.execute("""
        SELECT mode, status, COUNT(*) c
        FROM copied_trades
        WHERE submitted_at >= ?
        GROUP BY mode, status
    """, (cutoff,)).fetchall()

    skips = conn.execute("""
        SELECT rule, COUNT(*) c
        FROM skipped_trades
        WHERE ts >= ?
        GROUP BY rule
        ORDER BY c DESC
    """, (cutoff,)).fetchall()

    pnl = conn.execute("""
        SELECT
          COALESCE(SUM(realized_pnl_usdc), 0) AS realized,
          COALESCE(SUM(our_notional_usdc), 0) AS notional
        FROM copied_trades
        WHERE submitted_at >= ?
    """, (cutoff,)).fetchone()

    top_markets = conn.execute("""
        SELECT condition_id,
               COUNT(*) AS n,
               SUM(our_notional_usdc) AS exposure
        FROM copied_trades
        WHERE submitted_at >= ?
        GROUP BY condition_id
        ORDER BY exposure DESC
        LIMIT 10
    """, (cutoff,)).fetchall()

    errors = conn.execute(
        "SELECT COUNT(*) c FROM errors WHERE ts >= ?", (cutoff,)
    ).fetchone()["c"]

    print()
    print(f"  DRY-RUN REPORT — last {hours}h")
    print("  " + "-" * 50)
    print(f"  target trades observed: {targets}")
    print()
    print("  what we did:")
    if not copies:
        print("    (no copies)")
    for row in copies:
        print(f"    {row['mode']:5s}  {row['status']:9s}  {row['c']}")
    print(f"    realized PnL: ${pnl['realized']:.2f}   notional placed: ${pnl['notional']:.2f}")
    print()
    print("  what we skipped (by rule):")
    if not skips:
        print("    (no skips)")
    for row in skips:
        print(f"    {row['rule']:32s}  {row['c']}")
    print()
    print("  top markets by exposure:")
    if not top_markets:
        print("    (none)")
    for row in top_markets:
        print(f"    {row['condition_id'][:20]}..  n={row['n']}  exp=${row['exposure']:.2f}")
    print()
    print(f"  errors logged: {errors}")
    print()
