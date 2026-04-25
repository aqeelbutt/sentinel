"""SQLite storage. Sync API (sqlite3 + WAL). Streamlit, CLI, and the
scanner all touch the same DB; WAL mode lets them coexist safely.

The async strategy pipeline wraps repo calls in `asyncio.to_thread()`
when called from inside an event loop — see strategy.pipeline.
"""
