"""Domain types — pure data, no behavior beyond simple derived values.

Conventions:
  - Frozen dataclasses everywhere. Domain objects are immutable.
  - `Decimal` for every monetary value. Never `float` for money.
  - `StrEnum` for finite sets so SQL columns and JSON log fields read cleanly.
  - Timestamps are timezone-aware. Internal bus uses UTC; UI renders ET.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalName(StrEnum):
    GAP_AND_GO = "gap_and_go"
    MEAN_REVERSION = "mean_reversion"
    VWAP_RECLAIM = "vwap_reclaim"


class RecommendationAction(StrEnum):
    BUY = "buy"
    HOLD = "hold"
    SKIP = "skip"


class OrderStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class Bar:
    """OHLCV bar. `ts` is the bar's CLOSE timestamp (ET, tz-aware)."""
    symbol: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timeframe: str  # "1m", "5m", "1d" etc.


@dataclass(frozen=True)
class Quote:
    """Last-known NBBO-ish quote. yfinance won't give us real NBBO;
    we approximate with last trade + implied spread."""
    symbol: str
    ts: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        if mid <= 0:
            return float("inf")
        return float((self.ask - self.bid) / mid)


@dataclass(frozen=True)
class SignalFiring:
    """One signal evaluation at one bar. Stored in signals_log as a row."""
    name: SignalName
    symbol: str
    fired: bool
    strength: float               # [0, 1]; 0 means evaluated-but-not-firing
    ts: datetime
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolSentiment:
    """Aggregate sentiment for one symbol at one moment."""
    symbol: str
    score: float                  # [-1, 1]
    confidence: float             # [0, 1]
    article_count: int
    independent_sources: int
    newest_age_hours: float
    qualifies: bool
    computed_at: datetime
    contributing_article_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegimeState:
    """Macro gate snapshot."""
    spy_above_vwap: bool
    spy_intraday_change_pct: float
    qqq_intraday_change_pct: float
    vix_level: float
    vix_daily_change_pct: float
    macro_event_today: str | None  # "FOMC" | "CPI" | "NFP" | None
    macro_event_cooldown_ends: datetime | None
    hostile: bool                  # True → block new longs
    block_reasons: tuple[str, ...]
    computed_at: datetime


@dataclass(frozen=True)
class Recommendation:
    """What Sentinel produces. The UI shows a list of these; the user clicks
    'Add to Portfolio' to turn one into a VirtualBroker fill."""
    symbol: str
    action: RecommendationAction
    score: float                  # [0, 1] — overall confluence strength
    entry_price_ref: Decimal      # the quote used for sizing/display at rec time
    suggested_stop: Decimal
    suggested_take_profit: Decimal | None
    suggested_qty: int
    signals_fired: tuple[SignalName, ...]
    sentiment: SymbolSentiment | None
    regime: RegimeState | None
    rationale: str                # human-readable, shown in UI
    created_at: datetime
    expires_at: datetime          # recs stale after ~1 bar; UI greys them out


@dataclass(frozen=True)
class OrderIntent:
    """Strategy → Risk input. Symbol/side/stop already picked."""
    symbol: str
    side: Side
    signals_fired: tuple[SignalName, ...]
    sentiment_score: float
    entry_price: Decimal
    stop_price: Decimal
    take_profit: Decimal | None
    bar_ts: datetime
    source_recommendation_id: str | None = None


@dataclass(frozen=True)
class Fill:
    """What a broker tells us after an order executes."""
    client_order_id: str
    symbol: str
    side: Side
    qty: int
    price: Decimal
    ts: datetime
    commission: Decimal = Decimal("0")


@dataclass(frozen=True)
class Position:
    """An open position. P&L is unrealized; realized P&L lives in ClosedTrade."""
    symbol: str
    side: Side
    qty: int
    avg_entry_price: Decimal
    opened_at: datetime
    stop_price: Decimal | None
    take_profit: Decimal | None
    source_recommendation_id: str | None = None

    def notional(self, mark: Decimal) -> Decimal:
        return Decimal(self.qty) * mark

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        sign = Decimal(1) if self.side == Side.LONG else Decimal(-1)
        return sign * Decimal(self.qty) * (mark - self.avg_entry_price)

    def unrealized_pnl_pct(self, mark: Decimal) -> float:
        if self.avg_entry_price <= 0:
            return 0.0
        sign = 1.0 if self.side == Side.LONG else -1.0
        return sign * float((mark - self.avg_entry_price) / self.avg_entry_price)


@dataclass(frozen=True)
class ClosedTrade:
    """A completed round-trip. Immutable once written to trade_journal."""
    symbol: str
    side: Side
    qty: int
    entry_price: Decimal
    exit_price: Decimal
    opened_at: datetime
    closed_at: datetime
    realized_pnl: Decimal
    realized_pnl_pct: float
    close_reason: str             # "user" | "stop" | "trailing" | "time" | "profit_target"
    source_recommendation_id: str | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    mode: str                     # "virtual" in Phase 1
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    deployed_pct: float
    open_positions: int
    as_of: datetime
