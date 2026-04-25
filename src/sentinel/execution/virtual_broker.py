"""VirtualBroker — paper-only fills using current quote ± slippage.

Idempotency: Every order carries a deterministic client_order_id; a duplicate
submission is detected via the virtual_orders table and the original Fill is
returned. This mirrors the Alpaca-side dedupe Phase 2 will rely on.

Profit-take + auto-stop: VirtualBroker exposes `sweep_open_positions()` which
the scanner calls after every scan and the dashboard calls on every refresh.
For each open position it pulls the current quote, marks the position, and
closes it if `unrealized_pnl_pct >= profit_take_pct` (with profit_take_enabled)
or `<= -hard_stop_pct` (with auto_stop_enabled). All closes write a row to
trade_journal-equivalent (virtual_positions) with the close_reason.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sentinel.config.schema import Config
from sentinel.core.clock import Clock
from sentinel.core.identity import VirtualModeToken
from sentinel.core.types import AccountSnapshot, Fill, Position, Side
from sentinel.data.market import yfinance_feed
from sentinel.risk.manager import RiskDecision, compute_equity_state
from sentinel.storage.repos import decisions, orders, positions

log = logging.getLogger(__name__)

CloseReason = str  # "user" | "profit_take" | "hard_stop" | "trailing_stop" | "time" | "session_end"


class OrderRejected(RuntimeError):
    pass


class VirtualBroker:
    mode = "virtual"

    def __init__(
        self,
        cfg: Config,
        db_path: Path,
        clock: Clock,
        token: VirtualModeToken,
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self.clock = clock
        self._token = token  # reserved; mirrors Phase 2 LiveModeToken pattern

    # ---------- account / positions ----------

    def account(self) -> AccountSnapshot:
        eq = compute_equity_state(
            self.db_path,
            starting_equity=self.cfg.app.initial_virtual_equity,
            clock=self.clock,
        )
        open_pos = positions.list_open(self.db_path)
        deployed_pct = float(eq.deployed / eq.equity) if eq.equity > 0 else 0.0
        return AccountSnapshot(
            mode="virtual",
            equity=eq.equity,
            cash=eq.cash,
            buying_power=eq.cash,
            deployed_pct=deployed_pct,
            open_positions=len(open_pos),
            as_of=self.clock.now(),
        )

    def positions(self) -> list[Position]:
        rows = positions.list_open(self.db_path)
        return [positions.position_to_domain(r) for r in rows]

    # ---------- order submission ----------

    def submit_buy(
        self,
        *,
        symbol: str,
        qty: int,
        entry_price: Decimal,
        stop_price: Decimal | None,
        take_profit: Decimal | None,
        decision: RiskDecision,
        source_recommendation_id: str | None,
    ) -> Fill:
        if not decision.approved:
            raise OrderRejected(
                f"submit_buy refused: RiskDecision not approved (gate={decision.gate}, reasons={decision.reasons})"
            )
        if not decision.client_order_id:
            raise OrderRejected("decision.client_order_id is empty")

        # Idempotency check
        existing = orders.get(self.db_path, decision.client_order_id)
        if existing and existing["status"] == "filled":
            log.info("idempotent re-submit for client_order_id=%s; returning original fill", decision.client_order_id)
            return Fill(
                client_order_id=existing["client_order_id"],
                symbol=existing["symbol"],
                side=Side(existing["side"]),
                qty=int(existing["qty"]),
                price=Decimal(existing["fill_price"]),
                ts=datetime.fromisoformat(existing["fill_ts"]),
            )

        fill_price = self._apply_slippage(entry_price, side=Side.LONG)
        now = self.clock.now()

        pos_id = positions.open_position(
            self.db_path,
            symbol=symbol,
            side=Side.LONG,
            qty=qty,
            avg_entry_price=fill_price,
            opened_at=now,
            stop_price=stop_price,
            take_profit=take_profit,
            source_recommendation_id=source_recommendation_id,
        )
        orders.insert_filled(
            self.db_path,
            client_order_id=decision.client_order_id,
            symbol=symbol, side=Side.LONG.value, qty=qty,
            order_type="market", limit_price=None,
            submitted_at=now, fill_price=fill_price, fill_ts=now,
            position_id=pos_id,
        )
        decisions.log(self.db_path, "fill", symbol=symbol, payload={
            "client_order_id": decision.client_order_id, "side": "long",
            "qty": qty, "price": str(fill_price), "position_id": pos_id,
        }, ts=now)
        return Fill(
            client_order_id=decision.client_order_id,
            symbol=symbol, side=Side.LONG, qty=qty, price=fill_price, ts=now,
        )

    def submit_sell(
        self,
        *,
        symbol: str,
        position_id: str,
        exit_price: Decimal,
        close_reason: str,
        ts: datetime | None = None,
    ) -> Fill:
        # Look up the open position
        open_pos = positions.list_open(self.db_path)
        row = next((p for p in open_pos if p["id"] == position_id and p["symbol"] == symbol), None)
        if row is None:
            raise OrderRejected(f"no open position with id={position_id} symbol={symbol}")

        fill_ts = ts or self.clock.now()
        fill_price = self._apply_slippage(exit_price, side=Side.SHORT)  # selling: hits the bid
        qty = int(row["qty"])
        entry = Decimal(row["avg_entry_price"])
        realized_pnl = Decimal(qty) * (fill_price - entry)
        realized_pct = float((fill_price - entry) / entry) if entry > 0 else 0.0

        positions.close_position(
            self.db_path,
            position_id=position_id,
            exit_price=fill_price,
            closed_at=fill_ts,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pct,
            close_reason=close_reason,
        )
        decisions.log(self.db_path, "close", symbol=symbol, payload={
            "position_id": position_id, "qty": qty, "exit_price": str(fill_price),
            "realized_pnl": str(realized_pnl), "realized_pnl_pct": realized_pct,
            "reason": close_reason,
        }, ts=fill_ts)
        return Fill(
            client_order_id=f"close-{position_id}",
            symbol=symbol, side=Side.SHORT, qty=qty, price=fill_price, ts=fill_ts,
        )

    def liquidate_all(self, reason: str) -> list[Fill]:
        out: list[Fill] = []
        for p in positions.list_open(self.db_path):
            quote = yfinance_feed.latest_quote(p["symbol"])
            mark = quote.last if quote else Decimal(p["avg_entry_price"])
            try:
                f = self.submit_sell(
                    symbol=p["symbol"], position_id=p["id"],
                    exit_price=mark, close_reason=reason,
                )
                out.append(f)
            except OrderRejected as e:
                log.warning("liquidate failed for %s: %s", p["symbol"], e)
        return out

    # ---------- automatic profit-take + stop sweep ----------

    def sweep_open_positions(self) -> list[Fill]:
        """Layered exit sweep — runs every dashboard refresh and every `sentinel run` cycle.

        For each open position, in priority order:

        1. **Profit take** (+5% default) — take the win, period.
        2. **Trailing stop** — once peak >= +1.5%, exit if mark drops 0.75% below peak.
        3. **Break-even stop** — once peak >= +1%, exit if mark falls back to entry.
           (This is what makes 'once it's +1%, you can't lose money on it' true.)
        4. **Hard stop** (-1%) — only the initial backstop, until break-even arms.

        Every position carries a `peak_price` we update on each sweep. The peak
        is what activates the better stops; the current mark is what triggers
        the exit. So a position that touches +1.2% then comes back exits at
        break-even, even though it never reached +1.5%.
        """
        out: list[Fill] = []
        cfg = self.cfg.exit
        if not (cfg.profit_take_enabled or cfg.auto_stop_enabled
                or cfg.breakeven_stop_enabled or cfg.trailing_stop_enabled):
            return out

        for p in positions.list_open(self.db_path):
            quote = yfinance_feed.latest_quote(p["symbol"])
            if quote is None:
                continue
            entry = Decimal(p["avg_entry_price"])
            if entry <= 0:
                continue
            mark = quote.last
            peak = Decimal(p["peak_price"]) if p.get("peak_price") else entry
            if mark > peak:
                peak = mark
                positions.update_peak(self.db_path, p["id"], mark)
            mark_pct = float((mark - entry) / entry)
            peak_pct = float((peak - entry) / entry)

            close_reason: str | None = None

            # 1. Profit take — highest priority
            if cfg.profit_take_enabled and mark_pct >= cfg.profit_take_pct:
                close_reason = "profit_take"

            # 2. Trailing stop — only once we've made trailing_stop_activate_pct
            elif (cfg.trailing_stop_enabled
                  and peak_pct >= cfg.trailing_stop_activate_pct):
                trail_floor = peak * (Decimal("1") - Decimal(str(cfg.trailing_stop_pct)))
                if mark <= trail_floor:
                    close_reason = "trailing_stop"

            # 3. Break-even stop — once we've touched +breakeven_trigger_pct,
            #    exit if mark falls back to entry. After this fires, the worst
            #    possible outcome on this trade is roughly $0.
            elif (cfg.breakeven_stop_enabled
                  and peak_pct >= cfg.breakeven_trigger_pct):
                be_price = entry * (Decimal("1") + Decimal(str(cfg.breakeven_buffer_bps)) / Decimal("10000"))
                if mark <= be_price:
                    close_reason = "breakeven_stop"

            # 4. Initial hard stop — applies until break-even arms
            elif cfg.auto_stop_enabled and mark_pct <= -cfg.hard_stop_pct:
                close_reason = "hard_stop"

            if close_reason:
                try:
                    out.append(self.submit_sell(
                        symbol=p["symbol"], position_id=p["id"],
                        exit_price=mark, close_reason=close_reason,
                    ))
                    log.info(
                        "auto-close %s pnl=%.2f%% peak=%.2f%% reason=%s",
                        p["symbol"], mark_pct * 100, peak_pct * 100, close_reason,
                    )
                except OrderRejected as e:
                    log.warning("auto-close failed for %s: %s", p["symbol"], e)
        return out

    # ---------- helpers ----------

    def _apply_slippage(self, price: Decimal, *, side: Side) -> Decimal:
        bps = Decimal(str(self.cfg.virtual_broker.slippage_bps)) / Decimal("10000")
        if side == Side.LONG:
            return (price * (Decimal("1") + bps)).quantize(Decimal("0.0001"))
        return (price * (Decimal("1") - bps)).quantize(Decimal("0.0001"))
