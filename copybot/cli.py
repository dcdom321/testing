"""Argparse CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import dashboard, loop, persistence
from .config import load_config
from .market_data import GammaProvider
from .risk import CopyRiskGate


DEFAULT_CFG_PATH = "copybot/config.yaml"


def _open_runtime(cfg):
    """Returns (conn, gate). Used by status/dry-run/pause/kill commands."""
    conn = persistence.connect(cfg.db_path)
    persistence.run_migrations(conn, str(Path(__file__).resolve().parent / "migrations"))
    market = GammaProvider(base_url=cfg.gamma_api_url)
    gate = CopyRiskGate(cfg, conn, market)
    return conn, gate


def cmd_init_db(args):
    cfg = load_config(args.config)
    conn = persistence.connect(cfg.db_path)
    applied = persistence.run_migrations(conn, str(Path(__file__).resolve().parent / "migrations"))
    if not applied:
        print("  [DB] schema already up to date")
    else:
        print(f"  [DB] {len(applied)} migration(s) applied")
    conn.close()


def cmd_run(args):
    cfg = load_config(args.config)
    loop.run(cfg)


def cmd_status(args):
    cfg = load_config(args.config)
    conn, gate = _open_runtime(cfg)
    dashboard.print_status(conn, gate)
    conn.close()


def cmd_dry_run_report(args):
    cfg = load_config(args.config)
    conn, _ = _open_runtime(cfg)
    dashboard.dry_run_report(conn, hours=args.hours)
    conn.close()


def cmd_pause(args):
    cfg = load_config(args.config)
    _, gate = _open_runtime(cfg)
    gate.pause(reason=args.reason or "")
    print(f"  paused: {gate.pause_file}")


def cmd_resume(args):
    cfg = load_config(args.config)
    _, gate = _open_runtime(cfg)
    gate.resume()
    print("  resumed")


def cmd_kill(args):
    cfg = load_config(args.config)
    _, gate = _open_runtime(cfg)
    gate.trip_kill_switch(reason=args.reason or "")
    print(f"  kill switch tripped: {gate.kill_switch_file}")


def cmd_unkill(args):
    cfg = load_config(args.config)
    _, gate = _open_runtime(cfg)
    gate.reset_kill_switch()
    print("  kill switch cleared")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="copybot")
    p.add_argument("-c", "--config", default=DEFAULT_CFG_PATH,
                   help=f"path to config.yaml (default: {DEFAULT_CFG_PATH})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="apply migrations").set_defaults(func=cmd_init_db)
    sub.add_parser("run", help="start the main loop").set_defaults(func=cmd_run)
    sub.add_parser("status", help="show open positions and PnL").set_defaults(func=cmd_status)

    dr = sub.add_parser("dry-run-report",
                        help="what the bot would have done over the last N hours")
    dr.add_argument("--hours", type=int, default=24)
    dr.set_defaults(func=cmd_dry_run_report)

    pa = sub.add_parser("pause", help="halt new entries")
    pa.add_argument("--reason", default="")
    pa.set_defaults(func=cmd_pause)

    sub.add_parser("resume", help="lift the pause flag").set_defaults(func=cmd_resume)

    kl = sub.add_parser("kill", help="trip the kill switch")
    kl.add_argument("--reason", default="")
    kl.set_defaults(func=cmd_kill)

    sub.add_parser("unkill", help="clear the kill switch").set_defaults(func=cmd_unkill)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
