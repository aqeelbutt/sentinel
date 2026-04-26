"""RiskManager — sole gate between strategy and broker.

Invocation order within evaluate():
    KILLED → DAILY_LOSS → WEEKLY_LOSS → CONSECUTIVE_LOSSES
    → ORDER_RATE → CONCURRENT_MAX → PER_POSITION_MAX → DEPLOYED_MAX
    → CORRELATION → PRICE_SANITY → sizing → approved

Cheap/absolute checks come first so the 9:30 burst is fast. Sizing
clamps by both per-position and deployed caps; if the clamped qty is 0,
we return approved=False with the gate that caused the clamp.

Every evaluation writes a decisions_log row (kind='risk') so the post-90d
report can count exactly why each rejection happened.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from sentinel.config.schema import Config
from sentinel.core.clock import Clock
from sentinel.core.types import OrderIntent, Position, Side
from sentinel.risk import correlation, sizing
from sentinel.storage.repos import decisions, halts, positions


class Gate(StrEnum):
    KILLED = "killed"
    REGIME = "regime"
    DAILY_LOSS = "daily_loss"
    WEEKLY_LOSS = "weekly_loss"
    CONCURRENT_MAX = "concurrent_max"
    PER_POSITION_MAX = "per_position_max"
    DEPLOYED_MAX = "deployed_max"
    CORRELATION = "correlation"
    PRICE_SANITY = "price_sanity"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    ORDER_RATE = "order_rate"
    EARNINGS_BLACKOUT = "earnings_blackout"


class HaltScope(StrEnum):
    NEW_ENTRIES = "new_entries_only"
    ALL = "all_trading"


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: int
    stop_price: Decimal
    take_profit_price: Decimal | None
    gate: Gate | None
    reasons: tuple[str, ...]
    evaluated_at: datetime
    intent_hash: str
    client_order_id: str  # deterministic; VirtualBroker uses this for idempotency


@dataclass(frozen=True)
class EquityState:
    equity: Decimal
    cash: Decimal
    deployed: Decimal
    session_open_equity: Decimal


class RiskManager:
    def __init__(self, cfg: Config, db_path: Path, clock: Clock) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self.clock = clock

    def evaluate(self, intent: OrderIntent, equity: EquityState) -> RiskDecision:
        now = self.clock.now()
        reasons: list[str] = []

        # 1. KILLED
        if halts.is_halted(self.db_path, HaltScope.ALL.value):
            return self._reject(intent, now, Gate.KILLED, "all trading halted")
        if halts.is_halted(self.db_path, HaltScope.NEW_ENTRIES.value):
            return self._reject(intent, now, Gate.KILLED, "new entries halted")

        # 2. daily loss
        if equity.session_open_equity > 0:
            daily_pct = float((equity.equity - equity.session_open_equity) / equity.session_open_equity)
            if daily_pct <= -self.cfg.risk.daily_loss_limit_pct:
                halts.set_halt(self.db_path, HaltScope.NEW_ENTRIES.value, f"daily loss {daily_pct:.2%}")
                return self._reject(intent, now, Gate.DAILY_LOSS, f"daily drawdown {daily_pct:.2%}")

        # 3. weekly loss
        weekly_pct = self._rolling_pct_drawdown(self.cfg.risk.rolling_window_sessions)
        if weekly_pct is not None and weekly_pct <= -self.cfg.risk.weekly_loss_limit_pct:
            halts.set_halt(self.db_path, HaltScope.ALL.value, f"weekly loss {weekly_pct:.2%}")
            return self._reject(intent, now, Gate.WEEKLY_LOSS, f"weekly drawdown {weekly_pct:.2%}")

        # 4. consecutive losses
        if self._consecutive_losses() >= self.cfg.risk.consecutive_loss_halt:
            halts.set_halt(self.db_path, HaltScope.NEW_ENTRIES.value, "3 consecutive losses")
            return self._reject(intent, now, Gate.CONSECUTIVE_LOSSES, "consecutive-loss breaker tripped")

        # 5. order rate
        if self._orders_in_last(self.cfg.risk.order_rate_window_sec) >= self.cfg.risk.order_rate_max:
            halts.set_halt(self.db_path, HaltScope.NEW_ENTRIES.value, "order rate exceeded")
            return self._reject(intent, now, Gate.ORDER_RATE, "order-rate breaker tripped")

        # 6. position caps
        open_pos = positions.list_open(self.db_path)
        if len(open_pos) >= self.cfg.risk.max_concurrent_positions:
            return self._reject(intent, now, Gate.CONCURRENT_MAX, f"{len(open_pos)} open positions at cap")
        if any(p["symbol"] == intent.symbol and p["status"] == "open" for p in open_pos):
            return self._reject(intent, now, Gate.CONCURRENT_MAX, f"already have open position in {intent.symbol}")

        # 7. price sanity
        # (no live quote available from here — caller passes intent.entry_price as the
        #  recent quote, and we validate it against suggested stop sanity)
        if intent.stop_price <= 0 or intent.stop_price >= intent.entry_price:
            return self._reject(intent, now, Gate.PRICE_SANITY, "stop not below entry")

        # 7.5 earnings blackout — refuse new entries within N hours of next earnings.
        # Earnings = binary risk; this gate is what makes "losing not an option" achievable
        # in the cases where the market structurally guarantees a violent move.
        if self.cfg.risk.earnings_blackout_enabled:
            from sentinel.storage.repos import catalysts as _cat
            next_earn = _cat.next_event_for(self.db_path, intent.symbol, "earnings", now=now)
            if next_earn is not None:
                hours_until = (next_earn - now).total_seconds() / 3600
                if 0 < hours_until <= self.cfg.risk.earnings_blackout_hours:
                    return self._reject(
                        intent, now, Gate.EARNINGS_BLACKOUT,
                        f"earnings in {hours_until:.1f}h ({next_earn.date()})",
                    )

        # 8. correlation
        other_syms = [p["symbol"] for p in open_pos]
        corr_sym, corr_val = correlation.max_correlation_with(
            self.db_path,
            candidate=intent.symbol,
            existing=other_syms,
            lookback_days=self.cfg.risk.correlation_lookback_days,
        )
        if corr_sym and abs(corr_val) > self.cfg.risk.max_correlation:
            return self._reject(
                intent, now, Gate.CORRELATION,
                f"30d corr with {corr_sym} = {corr_val:.2f} > {self.cfg.risk.max_correlation}",
            )

        # 9. sizing
        raw_qty = sizing.size_by_stop(
            equity=equity.equity,
            risk_per_trade_pct=self.cfg.risk.risk_per_trade_pct,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
        )
        qty = sizing.clamp_by_position_cap(
            qty=raw_qty, entry_price=intent.entry_price, equity=equity.equity,
            max_per_position_pct=self.cfg.risk.max_per_position_pct,
        )
        if qty <= 0 and raw_qty > 0:
            return self._reject(intent, now, Gate.PER_POSITION_MAX, "position cap clamps qty to 0")
        qty = sizing.clamp_by_deployed_cap(
            qty=qty, entry_price=intent.entry_price,
            currently_deployed=equity.deployed, equity=equity.equity,
            max_deployed_pct=self.cfg.risk.max_deployed_pct,
        )
        if qty <= 0:
            return self._reject(intent, now, Gate.DEPLOYED_MAX, "deployed cap clamps qty to 0")

        intent_hash = _hash_intent(intent)
        coid = _client_order_id(intent_hash, now)

        decision = RiskDecision(
            approved=True,
            quantity=qty,
            stop_price=intent.stop_price,
            take_profit_price=intent.take_profit,
            gate=None,
            reasons=("passed all gates",),
            evaluated_at=now,
            intent_hash=intent_hash,
            client_order_id=coid,
        )
        decisions.log(self.db_path, "risk", symbol=intent.symbol, payload={
            "approved": True, "qty": qty, "client_order_id": coid,
            "intent_hash": intent_hash,
        }, ts=now)
        return decision

    # ---------- helpers ----------

    def _reject(self, intent: OrderIntent, now: datetime, gate: Gate, reason: str) -> RiskDecision:
        intent_hash = _hash_intent(intent)
        decision = RiskDecision(
            approved=False, quantity=0,
            stop_price=intent.stop_price, take_profit_price=intent.take_profit,
            gate=gate, reasons=(reason,),
            evaluated_at=now, intent_hash=intent_hash, client_order_id="",
        )
        decisions.log(self.db_path, "risk", symbol=intent.symbol, payload={
            "approved": False, "gate": gate.value, "reason": reason,
            "intent_hash": intent_hash,
        }, ts=now)
        return decision

    def _rolling_pct_drawdown(self, sessions: int) -> float | None:
        """Max drawdown across the last `sessions` daily equity snapshots."""
        from sentinel.storage.repos import equity_snapshots
        snaps = equity_snapshots.list_all(self.db_path)
        if len(snaps) < 2:
            return None
        recent = snaps[-sessions:]
        equities = [Decimal(s["equity"]) for s in recent]
        peak = equities[0]
        trough = equities[-1]
        for e in equities:
            if e > peak:
                peak = e
        if peak <= 0:
            return None
        return float((trough - peak) / peak)

    def _consecutive_losses(self) -> int:
        closed = positions.list_closed(self.db_path)
        streak = 0
        for row in closed:  # already ordered DESC by closed_at
            if row["realized_pnl"] is None:
                break
            if Decimal(row["realized_pnl"]) < 0:
                streak += 1
            else:
                break
        return streak

    def _orders_in_last(self, window_sec: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_sec)).isoformat()
        # uses decisions_log rows of kind='order'
        rows = decisions.list_recent(self.db_path, kind="order", limit=200)
        return sum(1 for r in rows if r["ts"] >= cutoff)


# ---------- module helpers ----------

def _hash_intent(intent: OrderIntent) -> str:
    payload = json.dumps({
        "symbol": intent.symbol, "side": intent.side.value,
        "entry_price": str(intent.entry_price), "stop_price": str(intent.stop_price),
        "tp": str(intent.take_profit) if intent.take_profit else None,
        "bar_ts": intent.bar_ts.isoformat(),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _client_order_id(intent_hash: str, now: datetime) -> str:
    seed = f"{intent_hash}|{now.date().isoformat()}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def compute_equity_state(db_path: Path, starting_equity: Decimal, clock: Clock) -> EquityState:
    """Derive current virtual equity from persisted state.

    equity = starting_equity + Σ realized_pnl + Σ unrealized_pnl (at last close).
    deployed = Σ qty * current_mark for open positions.
    cash     = equity - deployed.
    session_open_equity is the equity snapshot for today's session (or starting).
    """
    from sentinel.data.market import bar_store
    from sentinel.storage.repos import equity_snapshots

    open_pos = positions.list_open(db_path)
    closed = positions.list_closed(db_path)
    realized = sum((Decimal(p["realized_pnl"] or 0) for p in closed), start=Decimal("0"))

    deployed = Decimal("0")
    unrealized = Decimal("0")
    for p in open_pos:
        sym = p["symbol"]
        bars = bar_store.get_bars(db_path, sym, "1d", days=5)
        if not bars:
            continue
        mark = bars[-1].close
        qty = Decimal(p["qty"])
        deployed += qty * mark
        unrealized += qty * (mark - Decimal(p["avg_entry_price"]))

    equity = starting_equity + realized + unrealized
    cash = equity - deployed

    session_open_equity = equity
    snaps = equity_snapshots.list_all(db_path)
    if snaps:
        today = clock.now().date().isoformat()
        today_snap = next((s for s in snaps if s["snapshot_date"] == today), None)
        if today_snap:
            session_open_equity = Decimal(today_snap["equity"])
        else:
            session_open_equity = Decimal(snaps[-1]["equity"])
    return EquityState(equity=equity, cash=cash, deployed=deployed, session_open_equity=session_open_equity)
