"""Internal data shapes shared across copybot modules. No I/O."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Trade:
    """A trade observed on a target wallet's history."""
    tx_hash: str
    target_wallet: str
    asset_token_id: str
    condition_id: str
    side: str                 # 'BUY' | 'SELL'
    size: float               # shares
    price: float              # 0..1
    notional_usdc: float      # size * price
    ts: int                   # unix seconds, exchange timestamp
    outcome: Optional[str] = None
    outcome_index: Optional[int] = None
    title: Optional[str] = None
    slug: Optional[str] = None
    event_slug: Optional[str] = None


@dataclass(frozen=True)
class MarketSnapshot:
    """A point-in-time snapshot of a market's depth used for risk checks."""
    condition_id: str
    best_bid: float
    best_ask: float
    mid: float
    liquidity_usdc: float
    volume_usdc: float
    fetched_at: int


@dataclass
class Approve:
    """Risk decision: trade approved with the size and expected price we'll use."""
    our_size: float
    our_price: float
    mid: float
    slippage_pct: float


@dataclass
class Skip:
    """Risk decision: trade skipped. `rule` is a short stable identifier."""
    rule: str
    detail: str = ""
    observed_value: Optional[float] = None
    threshold: Optional[float] = None


Decision = object  # Approve | Skip — kept loose to avoid 3.10+ type-union dep


@dataclass(frozen=True)
class CopiedTrade:
    """The trade we actually placed (paper or real)."""
    target_trade_id: int
    mode: str                 # 'paper' | 'real'
    side: str
    asset_token_id: str
    condition_id: str
    our_size: float
    our_price: float
    our_notional_usdc: float
    copy_ratio: float
    client_order_id: str
    status: str               # 'pending' | 'filled' | 'partial' | 'rejected' | 'paper'
    submitted_at: int
    exchange_order_id: Optional[str] = None
    filled_size: float = 0.0
    filled_avg_price: Optional[float] = None
    realized_pnl_usdc: Optional[float] = None
    filled_at: Optional[int] = None
    raw_response: Optional[str] = None


@dataclass(frozen=True)
class SkipReason:
    """A rejected trade for the dry-run report."""
    target_trade_id: int
    rule: str
    detail: str
    ts: int
    observed_value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class Position:
    """Net position in a single CLOB token (aggregated across copied trades)."""
    condition_id: str
    asset_token_id: str
    size: float               # signed: + = long YES/NO outcome shares
    avg_cost: float           # cost basis per share
    opened_at: int
