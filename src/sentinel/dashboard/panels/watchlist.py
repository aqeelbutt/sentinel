"""Watchlist management."""
from __future__ import annotations

import streamlit as st

from sentinel.dashboard.state import get_config
from sentinel.storage.repos import watchlist


def render() -> None:
    cfg = get_config()
    st.title("Watchlist")
    st.caption("Symbols scanned each `sentinel scan` or 'Run scan now' click.")

    current = watchlist.list_all(cfg.storage.db_path)
    if not current:
        watchlist.replace_all(cfg.storage.db_path, cfg.watchlist.symbols)
        current = watchlist.list_all(cfg.storage.db_path)

    st.write(f"**{len(current)} symbols:** " + ", ".join(current))

    col1, col2 = st.columns(2)
    with col1:
        new_sym = st.text_input("Add symbol", placeholder="e.g. AAPL")
        if st.button("➕ Add", type="primary") and new_sym:
            watchlist.add(cfg.storage.db_path, new_sym)
            st.success(f"Added {new_sym.upper()}")
            st.rerun()

    with col2:
        rm = st.selectbox("Remove symbol", options=[""] + current)
        if st.button("➖ Remove") and rm:
            watchlist.remove(cfg.storage.db_path, rm)
            st.success(f"Removed {rm}")
            st.rerun()
