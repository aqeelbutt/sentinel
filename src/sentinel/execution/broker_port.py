"""BrokerPort Protocol — common surface for VirtualBroker and (Phase 2) AlpacaBroker."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sentinel.core.types import AccountSnapshot, Fill, Position, Side
from sentinel.risk.manager import RiskDecision


class BrokerPort(Protocol):
    mode: str

    def account(self) -> AccountSnapshot: ...
    def positions(self) -> list[Position]: ...
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
    ) -> Fill: ...
    def submit_sell(
        self,
        *,
        symbol: str,
        position_id: str,
        exit_price: Decimal,
        close_reason: str,
        ts: datetime | None = None,
    ) -> Fill: ...
    def liquidate_all(self, reason: str) -> list[Fill]: ...
