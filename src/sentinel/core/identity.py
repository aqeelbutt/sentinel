"""Paper/live mode resolution. Phase 1 always returns VIRTUAL; attempts to
switch to PAPER or LIVE raise so there is no accidental promotion path.

When Phase 2 lands, this module grows the triple-confirmation flow:
    1. env AMS_LIVE_TRADING == "I_ACCEPT_REAL_MONEY_RISK"
    2. CLI flag --i-understand-this-is-real-money
    3. user types the live account number; must match pre-flight check
Only then is a LiveModeToken issued, and constructors requiring one accept it
as a parameter — making "accidentally went live" a type error.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    VIRTUAL = "virtual"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class VirtualModeToken:
    """Marker passed into VirtualBroker. Trivially obtainable — no safety gates."""


@dataclass(frozen=True)
class LiveModeToken:
    """Phase 2 only. Opaque token; impossible to construct without
    confirm_live_mode() returning it. Not serializable."""
    _account_number_suffix: str = ""


class LiveModeNotPermitted(RuntimeError):
    """Raised if any Phase 1 code path is asked to enter live/paper mode."""


def resolve_virtual_token() -> VirtualModeToken:
    return VirtualModeToken()


def confirm_live_mode(*args: object, **kwargs: object) -> LiveModeToken:
    raise LiveModeNotPermitted(
        "Live mode is disabled in Phase 1. Broker integration is deferred; "
        "see CLAUDE.md section 'Phase 2 preview' for the required contract."
    )
