"""Sentinel — Triple-Check equity recommendation engine + virtual portfolio tracker.

Phase 1: no broker; recommendations flow to a VirtualBroker and a Streamlit UI.
See CLAUDE.md at the repo root for full context.
"""
from __future__ import annotations

__version__ = "0.1.0"

# Auto-load .env at package import so API keys are available everywhere
# (CLI, Streamlit, tests). Silently no-op if dotenv missing or no .env file.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass
