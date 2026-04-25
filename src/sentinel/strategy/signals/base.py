"""Signal protocol. Every signal is a callable taking bars + context,
returning a SignalFiring (fired=True/False + strength + inputs for audit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from sentinel.core.types import Bar, SignalFiring


@dataclass(frozen=True)
class SignalContext:
    symbol: str
    now: datetime
    intraday_5m: Sequence[Bar]
    daily: Sequence[Bar]


class Signal(Protocol):
    def name(self) -> str: ...
    def evaluate(self, ctx: SignalContext) -> SignalFiring: ...
