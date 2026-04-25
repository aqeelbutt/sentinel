"""Streamlit session-state helpers."""
from __future__ import annotations

from functools import lru_cache

import streamlit as st

from sentinel.config.schema import Config
from sentinel.core.clock import MarketClock
from sentinel.core.config import load_config
from sentinel.core.identity import resolve_virtual_token
from sentinel.execution.virtual_broker import VirtualBroker
from sentinel.risk.manager import RiskManager
from sentinel.storage.db import init_db


@st.cache_resource
def get_config() -> Config:
    return load_config()


@st.cache_resource
def get_clock() -> MarketClock:
    return MarketClock()


@st.cache_resource
def get_broker() -> VirtualBroker:
    cfg = get_config()
    init_db(cfg.storage.db_path, wal=cfg.storage.wal)
    return VirtualBroker(cfg, cfg.storage.db_path, get_clock(), resolve_virtual_token())


@st.cache_resource
def get_risk_manager() -> RiskManager:
    cfg = get_config()
    return RiskManager(cfg, cfg.storage.db_path, get_clock())


def db_path() -> str:
    return str(get_config().storage.db_path)
