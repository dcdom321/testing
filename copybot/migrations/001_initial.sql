-- Initial schema for copybot. Idempotent: applied iff schema_version row absent.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS target_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash         TEXT    NOT NULL UNIQUE,
    target_wallet   TEXT    NOT NULL,
    asset_token_id  TEXT    NOT NULL,
    condition_id    TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
    size            REAL    NOT NULL,
    price           REAL    NOT NULL,
    notional_usdc   REAL    NOT NULL,
    outcome         TEXT,
    outcome_index   INTEGER,
    title           TEXT,
    slug            TEXT,
    event_slug      TEXT,
    ts              INTEGER NOT NULL,
    observed_at     INTEGER NOT NULL,
    raw_json        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_target_trades_wallet_ts ON target_trades(target_wallet, ts);
CREATE INDEX IF NOT EXISTS idx_target_trades_condition ON target_trades(condition_id);
CREATE INDEX IF NOT EXISTS idx_target_trades_ts        ON target_trades(ts);

CREATE TABLE IF NOT EXISTS copied_trades (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    target_trade_id    INTEGER NOT NULL REFERENCES target_trades(id),
    mode               TEXT    NOT NULL CHECK (mode IN ('paper','real')),
    side               TEXT    NOT NULL,
    asset_token_id     TEXT    NOT NULL,
    condition_id       TEXT    NOT NULL,
    our_size           REAL    NOT NULL,
    our_price          REAL    NOT NULL,
    our_notional_usdc  REAL    NOT NULL,
    copy_ratio         REAL    NOT NULL,
    client_order_id    TEXT    NOT NULL UNIQUE,
    exchange_order_id  TEXT,
    status             TEXT    NOT NULL CHECK (status IN ('pending','filled','partial','rejected','paper')),
    filled_size        REAL    DEFAULT 0,
    filled_avg_price   REAL,
    realized_pnl_usdc  REAL,
    submitted_at       INTEGER NOT NULL,
    filled_at          INTEGER,
    raw_response       TEXT
);
CREATE INDEX IF NOT EXISTS idx_copied_condition  ON copied_trades(condition_id);
CREATE INDEX IF NOT EXISTS idx_copied_status     ON copied_trades(status);
CREATE INDEX IF NOT EXISTS idx_copied_submitted  ON copied_trades(submitted_at);

CREATE TABLE IF NOT EXISTS skipped_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_trade_id INTEGER NOT NULL REFERENCES target_trades(id),
    rule            TEXT    NOT NULL,
    detail          TEXT,
    observed_value  REAL,
    threshold       REAL,
    ts              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skipped_rule ON skipped_trades(rule);
CREATE INDEX IF NOT EXISTS idx_skipped_ts   ON skipped_trades(ts);

CREATE TABLE IF NOT EXISTS errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    context_json TEXT,
    ts           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_source_ts ON errors(source, ts);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    cfg_json TEXT    NOT NULL,
    cfg_hash TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_cursors (
    wallet         TEXT    PRIMARY KEY,
    last_seen_ts   INTEGER NOT NULL,
    last_polled_at INTEGER NOT NULL,
    last_error     TEXT
);
