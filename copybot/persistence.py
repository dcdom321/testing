"""SQLite connection, migration runner, and narrow repo functions."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from .models import CopiedTrade, SkipReason, Trade


MIGRATION_RE = re.compile(r"^(\d+)_.+\.sql$")


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we use explicit txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def run_migrations(conn: sqlite3.Connection, migrations_dir: str) -> list:
    """Apply any unapplied migrations from `migrations_dir`. Idempotent."""
    p = Path(migrations_dir)
    files = sorted(f for f in p.iterdir() if MIGRATION_RE.match(f.name))

    # Bootstrap schema_version table if needed (the first migration creates it
    # too, but we need it before we can read it).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )
    """)
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_version")}

    just_applied = []
    for f in files:
        m = MIGRATION_RE.match(f.name)
        version = int(m.group(1))
        if version in applied:
            continue
        sql = f.read_text()
        # executescript issues an implicit COMMIT, so it can't sit inside our
        # transaction context manager. The migration SQL is idempotent
        # (CREATE TABLE IF NOT EXISTS), so a crash between the script and the
        # version insert just causes the same migration to apply again.
        conn.executescript(sql)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES(?, ?)",
            (version, int(time.time())),
        )
        just_applied.append(f.name)
        print(f"  [DB] applied migration {f.name}")
    return just_applied


# ----- repo functions ------------------------------------------------------

def insert_target_trade(conn, trade: Trade, raw_json: str) -> int:
    """Insert a target trade. Returns the row id, or the existing id if
    `tx_hash` already present."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO target_trades
            (tx_hash, target_wallet, asset_token_id, condition_id, side, size,
             price, notional_usdc, outcome, outcome_index, title, slug,
             event_slug, ts, observed_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.tx_hash, trade.target_wallet, trade.asset_token_id,
            trade.condition_id, trade.side, trade.size, trade.price,
            trade.notional_usdc, trade.outcome, trade.outcome_index,
            trade.title, trade.slug, trade.event_slug, trade.ts,
            int(time.time()), raw_json,
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT id FROM target_trades WHERE tx_hash = ?", (trade.tx_hash,)
    ).fetchone()
    return int(row["id"])


def insert_copied_trade(conn, ct: CopiedTrade) -> int:
    cur = conn.execute(
        """
        INSERT INTO copied_trades
            (target_trade_id, mode, side, asset_token_id, condition_id,
             our_size, our_price, our_notional_usdc, copy_ratio,
             client_order_id, exchange_order_id, status, filled_size,
             filled_avg_price, realized_pnl_usdc, submitted_at, filled_at,
             raw_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ct.target_trade_id, ct.mode, ct.side, ct.asset_token_id,
            ct.condition_id, ct.our_size, ct.our_price, ct.our_notional_usdc,
            ct.copy_ratio, ct.client_order_id, ct.exchange_order_id, ct.status,
            ct.filled_size, ct.filled_avg_price, ct.realized_pnl_usdc,
            ct.submitted_at, ct.filled_at, ct.raw_response,
        ),
    )
    return int(cur.lastrowid)


def insert_skipped_trade(conn, sr: SkipReason) -> int:
    cur = conn.execute(
        """
        INSERT INTO skipped_trades
            (target_trade_id, rule, detail, observed_value, threshold, ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (sr.target_trade_id, sr.rule, sr.detail, sr.observed_value,
         sr.threshold, sr.ts),
    )
    return int(cur.lastrowid)


def insert_error(conn, source: str, message: str, context: Optional[dict] = None) -> int:
    cur = conn.execute(
        "INSERT INTO errors(source, message, context_json, ts) VALUES (?, ?, ?, ?)",
        (source, message, json.dumps(context) if context else None, int(time.time())),
    )
    return int(cur.lastrowid)


def get_wallet_cursor(conn, wallet: str) -> Optional[int]:
    row = conn.execute(
        "SELECT last_seen_ts FROM wallet_cursors WHERE wallet = ?", (wallet,)
    ).fetchone()
    return int(row["last_seen_ts"]) if row else None


def update_wallet_cursor(conn, wallet: str, last_seen_ts: int, error: Optional[str] = None):
    conn.execute(
        """
        INSERT INTO wallet_cursors(wallet, last_seen_ts, last_polled_at, last_error)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(wallet) DO UPDATE SET
            last_seen_ts   = excluded.last_seen_ts,
            last_polled_at = excluded.last_polled_at,
            last_error     = excluded.last_error
        """,
        (wallet, last_seen_ts, int(time.time()), error),
    )


def get_market_exposure(conn, condition_id: str) -> float:
    """Sum of (filled or pending) BUY notional minus SELL notional for a market."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE side WHEN 'BUY' THEN our_notional_usdc ELSE -our_notional_usdc END
        ), 0) AS exposure
        FROM copied_trades
        WHERE condition_id = ? AND status IN ('paper','pending','partial','filled')
        """,
        (condition_id,),
    ).fetchone()
    return float(row["exposure"] or 0.0)


def get_today_realized_loss(conn) -> float:
    """Sum of *negative* realized PnL on copied trades closed today (UTC)."""
    today_start = _utc_day_start()
    row = conn.execute(
        """
        SELECT COALESCE(-SUM(realized_pnl_usdc), 0) AS loss
        FROM copied_trades
        WHERE realized_pnl_usdc IS NOT NULL
          AND realized_pnl_usdc < 0
          AND filled_at >= ?
        """,
        (today_start,),
    ).fetchone()
    return float(row["loss"] or 0.0)


def has_open_long(conn, asset_token_id: str) -> bool:
    """True iff our net BUY size for this token is positive."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE side WHEN 'BUY' THEN filled_size ELSE -filled_size END
        ), 0) AS net
        FROM copied_trades
        WHERE asset_token_id = ? AND status IN ('paper','filled','partial')
        """,
        (asset_token_id,),
    ).fetchone()
    return (row["net"] or 0) > 0


def snapshot_config(conn, cfg_dict: dict) -> int:
    cfg_json = json.dumps(cfg_dict, sort_keys=True, default=str)
    cfg_hash = hashlib.sha256(cfg_json.encode()).hexdigest()
    cur = conn.execute(
        "INSERT INTO config_snapshots(ts, cfg_json, cfg_hash) VALUES (?, ?, ?)",
        (int(time.time()), cfg_json, cfg_hash),
    )
    return int(cur.lastrowid)


def _utc_day_start() -> int:
    """Unix-seconds timestamp of today's 00:00:00 UTC."""
    now = int(time.time())
    return now - (now % 86400)
