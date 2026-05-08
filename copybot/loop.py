"""Tick orchestration + long-running run loop.

One pass:  ingest -> normalize -> for each new trade:
                       risk.evaluate -> engine.execute -> persist -> notify
                       update wallet cursor

Single thread, single process. Honors kill/pause flags between trades, never
mid-trade. KeyboardInterrupt cleanly closes the DB and prints a summary.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from typing import Optional

import requests

from . import persistence
from .config import Config
from .ingestion import WalletPoller
from .market_data import GammaProvider
from .models import Approve, CopiedTrade, Skip, SkipReason, Trade
from .normalize import normalize_trade
from .notifications import Notifier
from .paper import PaperEngine
from .risk import CopyRiskGate


def _resolve_copy_percent(cfg: Config, wallet: str) -> float:
    for t in cfg.targets:
        if t.wallet == wallet.lower() and t.copy_percent is not None:
            return float(t.copy_percent)
    return cfg.risk.copy_trade_percent


def _client_order_id(target_tx: str) -> str:
    """Idempotency key. Short enough to satisfy CLOB and unique enough for our
    own dedupe. Includes a slice of the target tx so we can tie back."""
    return f"copybot-{target_tx[:10]}-{uuid.uuid4().hex[:6]}"


def tick(cfg: Config, conn, gate: CopyRiskGate, paper: PaperEngine,
         exec_engine, notifier: Notifier, pollers: list) -> dict:
    """One pass over all target wallets. Returns counters."""
    counters = {"target": 0, "copied": 0, "skipped": 0, "errors": 0}

    for poller in pollers:
        wallet = poller.wallet
        cursor = persistence.get_wallet_cursor(conn, wallet)
        try:
            raw_trades = poller.poll(since_ts=cursor)
        except Exception as e:
            persistence.insert_error(conn, "ingestion", str(e), {"wallet": wallet})
            persistence.update_wallet_cursor(conn, wallet, cursor or 0, error=str(e))
            counters["errors"] += 1
            notifier.error("ingestion", f"{wallet[:10]}.. {e}")
            continue

        max_ts = cursor or 0
        for raw in raw_trades:
            counters["target"] += 1
            trade = normalize_trade(raw)
            if trade is None:
                # malformed payload — record as an error but don't stop
                persistence.insert_error(conn, "normalize", "rejected", {"raw": raw})
                continue

            with persistence.transaction(conn):
                tid = persistence.insert_target_trade(
                    conn, trade, raw_json=json.dumps(raw, default=str)
                )

            notifier.target_trade(trade)
            print(f"  [TARGET] {trade.target_wallet[:10]}.. {trade.side} "
                  f"{trade.size:.0f} @ ${trade.price:.3f} "
                  f"({trade.notional_usdc:.2f} USDC)")

            copy_pct = _resolve_copy_percent(cfg, trade.target_wallet)
            try:
                decision = gate.evaluate(trade, copy_pct)
            except Exception as e:
                persistence.insert_error(conn, "risk", str(e),
                                         {"target_trade_id": tid})
                counters["errors"] += 1
                notifier.error("risk", str(e))
                if trade.ts > max_ts:
                    max_ts = trade.ts
                continue

            if isinstance(decision, Skip):
                sr = SkipReason(
                    target_trade_id=tid, rule=decision.rule,
                    detail=decision.detail or "", ts=int(time.time()),
                    observed_value=decision.observed_value,
                    threshold=decision.threshold,
                )
                persistence.insert_skipped_trade(conn, sr)
                counters["skipped"] += 1
                print(f"    [SKIP] {decision.rule}: {decision.detail}")
                notifier.trade_skipped(sr, trade)
                if decision.rule in ("max_daily_loss", "kill_switch_active"):
                    notifier.risk_hit(decision.rule, decision.detail)
                if trade.ts > max_ts:
                    max_ts = trade.ts
                continue

            # Approve: paper or real
            assert isinstance(decision, Approve)
            client_order_id = _client_order_id(trade.tx_hash)
            engine = paper if cfg.paper_trading else exec_engine
            try:
                ct = engine.execute(tid, trade, decision, copy_pct, client_order_id)
            except Exception as e:
                persistence.insert_error(conn, "execution", str(e),
                                         {"target_trade_id": tid,
                                          "client_order_id": client_order_id})
                counters["errors"] += 1
                notifier.error("execution", str(e))
                if trade.ts > max_ts:
                    max_ts = trade.ts
                continue

            counters["copied"] += 1
            tag = "PAPER" if cfg.paper_trading else "LIVE"
            print(f"    [COPY {tag}] {ct.side} {ct.our_size:.2f} @ "
                  f"${ct.our_price:.3f} = ${ct.our_notional_usdc:.2f} ({ct.status})")
            notifier.trade_copied(ct)
            if trade.ts > max_ts:
                max_ts = trade.ts

        persistence.update_wallet_cursor(conn, wallet, max_ts)

    return counters


def run(cfg: Config) -> None:
    print()
    print("  COPYBOT — STARTING")
    mode = "LIVE" if cfg.real_trading_enabled else "PAPER"
    print(f"  mode:    {mode}")
    print(f"  targets: {len(cfg.targets)}")
    for t in cfg.targets:
        label = f" ({t.label})" if t.label else ""
        pct = (t.copy_percent if t.copy_percent is not None else cfg.risk.copy_trade_percent)
        print(f"    {t.wallet}{label}  copy_pct={pct:.2%}")
    print(f"  poll:    every {cfg.poll_interval_seconds}s")
    print(f"  db:      {cfg.db_path}")
    print()

    conn = persistence.connect(cfg.db_path)
    persistence.run_migrations(conn, str(_migrations_dir()))
    persistence.snapshot_config(conn, _serializable_cfg(cfg))

    market_data = GammaProvider(base_url=cfg.gamma_api_url)
    gate = CopyRiskGate(cfg, conn, market_data)
    paper = PaperEngine(conn)
    notifier = Notifier(cfg)

    exec_engine = None
    if cfg.real_trading_enabled:
        from .execution import ExecutionEngine, build_client
        client = build_client(cfg)
        exec_engine = ExecutionEngine(conn, client)

    session = requests.Session()
    pollers = [WalletPoller(cfg.data_api_url, t.wallet, session=session)
               for t in cfg.targets]

    try:
        while True:
            t0 = time.time()
            try:
                c = tick(cfg, conn, gate, paper, exec_engine, notifier, pollers)
                print(f"  [TICK] target={c['target']} copied={c['copied']} "
                      f"skipped={c['skipped']} errors={c['errors']} "
                      f"({time.time()-t0:.2f}s)")
            except Exception as e:
                persistence.insert_error(conn, "loop", str(e))
                notifier.error("loop", str(e))
                print(f"  [LOOP-ERR] {e}")
            time.sleep(cfg.poll_interval_seconds)
    except KeyboardInterrupt:
        print("\n  shutting down...")
    finally:
        conn.close()


def _migrations_dir():
    from pathlib import Path
    return Path(__file__).resolve().parent / "migrations"


def _serializable_cfg(cfg: Config) -> dict:
    """Strip secrets before snapshotting to DB."""
    d = {
        "paper_trading": cfg.paper_trading,
        "real_trading_enabled": cfg.real_trading_enabled,
        "poll_interval_seconds": cfg.poll_interval_seconds,
        "data_api_url": cfg.data_api_url,
        "gamma_api_url": cfg.gamma_api_url,
        "targets": [{"wallet": t.wallet, "label": t.label,
                     "copy_percent": t.copy_percent} for t in cfg.targets],
        "risk": asdict(cfg.risk),
        "wallet": {
            "host": cfg.wallet.host,
            "chain_id": cfg.wallet.chain_id,
            "signature_type": cfg.wallet.signature_type,
            "private_key": "***REDACTED***",
            "private_key_env": cfg.wallet.private_key_env,
            "funder_address": cfg.wallet.funder_address,
            "funder_address_env": cfg.wallet.funder_address_env,
        },
        "notifications": asdict(cfg.notifications),
    }
    return d
