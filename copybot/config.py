"""Config loader: YAML for rules, .env for secrets.

Single source of truth — every module receives the loaded `Config`, never
re-reads files. Defaults from the spec live as constants here.

`REAL_TRADING_ENABLED` is checked both in YAML and as an env var; if either
is not literally the string "true", paper mode is forced regardless of any
other setting. This is a deliberate belt-and-suspenders against a YAML
typo running real money.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Spec defaults
DEFAULT_COPY_TRADE_PERCENT             = 0.05
DEFAULT_MAX_TRADE_USDC                 = 10.0
DEFAULT_MAX_DAILY_LOSS_USDC            = 25.0
DEFAULT_MAX_MARKET_EXPOSURE_USDC       = 50.0
DEFAULT_MAX_SLIPPAGE_PERCENT           = 2.0
DEFAULT_MAX_PRICE_MOVE_AFTER_TARGET    = 3.0
DEFAULT_MIN_MARKET_LIQUIDITY_USDC      = 1000.0
DEFAULT_POLL_INTERVAL_SECONDS          = 30
DEFAULT_DATA_API_URL                   = "https://data-api.polymarket.com"
DEFAULT_GAMMA_API_URL                  = "https://gamma-api.polymarket.com"
DEFAULT_DB_PATH                        = "copybot/data/copybot.db"
DEFAULT_KILL_SWITCH_FILE               = "copybot/data/KILL_SWITCH"
DEFAULT_PAUSE_FILE                     = "copybot/data/PAUSED"


@dataclass(frozen=True)
class TargetCfg:
    wallet: str
    label: str = ""
    copy_percent: Optional[float] = None    # overrides global if set


@dataclass(frozen=True)
class RiskCfg:
    copy_trade_percent: float           = DEFAULT_COPY_TRADE_PERCENT
    max_trade_usdc: float               = DEFAULT_MAX_TRADE_USDC
    max_daily_loss_usdc: float          = DEFAULT_MAX_DAILY_LOSS_USDC
    max_market_exposure_usdc: float     = DEFAULT_MAX_MARKET_EXPOSURE_USDC
    max_slippage_percent: float         = DEFAULT_MAX_SLIPPAGE_PERCENT
    max_price_move_after_target: float  = DEFAULT_MAX_PRICE_MOVE_AFTER_TARGET
    min_market_liquidity_usdc: float    = DEFAULT_MIN_MARKET_LIQUIDITY_USDC
    cap_oversize_trades: bool           = True       # if false, skip oversize instead of capping
    blacklist_condition_ids: tuple      = ()
    whitelist_condition_ids: tuple      = ()         # empty = no restriction
    whitelist_categories: tuple         = ()
    kill_switch_file: str               = DEFAULT_KILL_SWITCH_FILE
    pause_file: str                     = DEFAULT_PAUSE_FILE


@dataclass(frozen=True)
class WalletCfg:
    """Wallet for real-mode execution. Reused by parent polymarket_client."""
    host: str               = "https://clob.polymarket.com"
    chain_id: int           = 137
    signature_type: int     = 2
    private_key_env: str    = "POLYMARKET_PRIVATE_KEY"
    funder_address_env: str = "POLYMARKET_FUNDER_ADDRESS"
    private_key: str        = ""        # rarely set; env var preferred
    funder_address: str     = ""


@dataclass(frozen=True)
class NotificationsCfg:
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    telegram_token_env: str  = "TELEGRAM_BOT_TOKEN"
    telegram_chat_env: str   = "TELEGRAM_CHAT_ID"
    notify_on_copy: bool     = True
    notify_on_skip: bool     = False    # noisy by default
    notify_on_target: bool   = True
    notify_on_error: bool    = True
    notify_on_risk_hit: bool = True


@dataclass(frozen=True)
class Config:
    paper_trading: bool                    = True
    real_trading_enabled: bool             = False
    poll_interval_seconds: int             = DEFAULT_POLL_INTERVAL_SECONDS
    data_api_url: str                      = DEFAULT_DATA_API_URL
    gamma_api_url: str                     = DEFAULT_GAMMA_API_URL
    db_path: str                           = DEFAULT_DB_PATH
    targets: tuple                         = ()       # tuple[TargetCfg, ...]
    risk: RiskCfg                          = field(default_factory=RiskCfg)
    wallet: WalletCfg                      = field(default_factory=WalletCfg)
    notifications: NotificationsCfg        = field(default_factory=NotificationsCfg)


def _coerce_targets(raw):
    out = []
    for t in raw or ():
        if isinstance(t, str):
            out.append(TargetCfg(wallet=t.lower()))
        else:
            out.append(TargetCfg(
                wallet=str(t["wallet"]).lower(),
                label=t.get("label", ""),
                copy_percent=t.get("copy_percent"),
            ))
    return tuple(out)


def _coerce_risk(raw):
    raw = raw or {}
    return RiskCfg(
        copy_trade_percent          = float(raw.get("copy_trade_percent",          DEFAULT_COPY_TRADE_PERCENT)),
        max_trade_usdc              = float(raw.get("max_trade_usdc",              DEFAULT_MAX_TRADE_USDC)),
        max_daily_loss_usdc         = float(raw.get("max_daily_loss_usdc",         DEFAULT_MAX_DAILY_LOSS_USDC)),
        max_market_exposure_usdc    = float(raw.get("max_market_exposure_usdc",    DEFAULT_MAX_MARKET_EXPOSURE_USDC)),
        max_slippage_percent        = float(raw.get("max_slippage_percent",        DEFAULT_MAX_SLIPPAGE_PERCENT)),
        max_price_move_after_target = float(raw.get("max_price_move_after_target", DEFAULT_MAX_PRICE_MOVE_AFTER_TARGET)),
        min_market_liquidity_usdc   = float(raw.get("min_market_liquidity_usdc",   DEFAULT_MIN_MARKET_LIQUIDITY_USDC)),
        cap_oversize_trades         = bool(raw.get("cap_oversize_trades",          True)),
        blacklist_condition_ids     = tuple(raw.get("blacklist_condition_ids", ())),
        whitelist_condition_ids     = tuple(raw.get("whitelist_condition_ids", ())),
        whitelist_categories        = tuple(raw.get("whitelist_categories",    ())),
        kill_switch_file            = str(raw.get("kill_switch_file", DEFAULT_KILL_SWITCH_FILE)),
        pause_file                  = str(raw.get("pause_file",       DEFAULT_PAUSE_FILE)),
    )


def _coerce_wallet(raw):
    raw = raw or {}
    return WalletCfg(
        host=str(raw.get("host", "https://clob.polymarket.com")),
        chain_id=int(raw.get("chain_id", 137)),
        signature_type=int(raw.get("signature_type", 2)),
        private_key_env=str(raw.get("private_key_env", "POLYMARKET_PRIVATE_KEY")),
        funder_address_env=str(raw.get("funder_address_env", "POLYMARKET_FUNDER_ADDRESS")),
        private_key=str(raw.get("private_key", "")),
        funder_address=str(raw.get("funder_address", "")),
    )


def _coerce_notifications(raw):
    raw = raw or {}
    return NotificationsCfg(
        discord_webhook_env=str(raw.get("discord_webhook_env", "DISCORD_WEBHOOK_URL")),
        telegram_token_env=str(raw.get("telegram_token_env", "TELEGRAM_BOT_TOKEN")),
        telegram_chat_env=str(raw.get("telegram_chat_env", "TELEGRAM_CHAT_ID")),
        notify_on_copy=bool(raw.get("notify_on_copy", True)),
        notify_on_skip=bool(raw.get("notify_on_skip", False)),
        notify_on_target=bool(raw.get("notify_on_target", True)),
        notify_on_error=bool(raw.get("notify_on_error", True)),
        notify_on_risk_hit=bool(raw.get("notify_on_risk_hit", True)),
    )


def load_config(path: str = "copybot/config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"config not found: {path}. Copy copybot/config.yaml.example to {path}."
        )
    raw = yaml.safe_load(p.read_text()) or {}

    # paper-trading override: any falsy interpretation forces paper.
    yaml_real = bool(raw.get("real_trading_enabled", False))
    env_real = os.environ.get("REAL_TRADING_ENABLED", "").strip().lower() == "true"
    real_enabled = yaml_real and env_real
    paper = not real_enabled

    if not yaml_real and env_real:
        print("[CONFIG] REAL_TRADING_ENABLED=true in env but real_trading_enabled is "
              "false in config.yaml — staying in paper mode.")
    if yaml_real and not env_real:
        print("[CONFIG] real_trading_enabled is true in config.yaml but "
              "REAL_TRADING_ENABLED env var is not 'true' — staying in paper mode.")

    return Config(
        paper_trading=paper,
        real_trading_enabled=real_enabled,
        poll_interval_seconds=int(raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)),
        data_api_url=str(raw.get("data_api_url", DEFAULT_DATA_API_URL)),
        gamma_api_url=str(raw.get("gamma_api_url", DEFAULT_GAMMA_API_URL)),
        db_path=str(raw.get("db_path", DEFAULT_DB_PATH)),
        targets=_coerce_targets(raw.get("targets")),
        risk=_coerce_risk(raw.get("risk")),
        wallet=_coerce_wallet(raw.get("wallet")),
        notifications=_coerce_notifications(raw.get("notifications")),
    )


def parent_polymarket_client_cfg(cfg: Config) -> dict:
    """Adapter: shape the parent polymarket_client.from_config expects."""
    w = cfg.wallet
    return {
        "live_trading": cfg.real_trading_enabled,
        "wallet": {
            "host":               w.host,
            "chain_id":           w.chain_id,
            "signature_type":     w.signature_type,
            "private_key":        w.private_key,
            "private_key_env":    w.private_key_env,
            "funder_address":     w.funder_address,
            "funder_address_env": w.funder_address_env,
        },
    }
