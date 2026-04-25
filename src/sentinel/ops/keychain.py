"""Secret storage.

Primary: macOS Keychain via `keyring`. Falls back to env vars for CI / non-mac
environments. Secrets never touch the config file or anything committed.

Usage:
    # One-time setup
    sentinel keychain set newsapi <your-key>
    sentinel keychain set finnhub <your-key>

    # From code
    from sentinel.ops.keychain import get_secret
    key = get_secret("newsapi")

Phase 2 broker credentials (alpaca_key_id, alpaca_secret) will use the same
helper — no env-var path for those because live trading keys must never sit
in a shell history.
"""
from __future__ import annotations

import logging
import os

import keyring

log = logging.getLogger(__name__)

SERVICE = "sentinel"

# Known secret names → env var fallback. Adding a new secret? Register it here.
_ENV_FALLBACKS = {
    "newsapi": "NEWSAPI_KEY",
    "finnhub": "FINNHUB_KEY",
    "alpaca_key_id": "ALPACA_KEY_ID",        # reserved — Phase 2, no fallback in live mode
    "alpaca_secret":  "ALPACA_SECRET_KEY",   # reserved — Phase 2
}


def get_secret(name: str) -> str | None:
    """Return the secret value or None. Keychain first, env var second."""
    try:
        val = keyring.get_password(SERVICE, name)
        if val:
            return val
    except Exception as e:  # noqa: BLE001 — keyring backends can misbehave on some systems
        log.debug("keyring lookup failed for %s: %s", name, e)
    env = _ENV_FALLBACKS.get(name, name.upper())
    return os.environ.get(env)


def set_secret(name: str, value: str) -> None:
    """Store `value` under `name` in the OS keychain."""
    keyring.set_password(SERVICE, name, value)


def remove_secret(name: str) -> None:
    """Delete `name` from the OS keychain. Silent if absent."""
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def known_secret_names() -> list[str]:
    """The set of secret names Sentinel knows how to look up. Doesn't imply
    they are set — use `get_secret(name)` to check that."""
    return sorted(_ENV_FALLBACKS.keys())
