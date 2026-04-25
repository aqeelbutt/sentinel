"""SQLite connection management + migration runner.

Connection style: short-lived connections via context manager. SQLite + WAL is
fast enough that pooling is unnecessary for this workload, and it keeps the
threading story simple (Streamlit reruns scripts on every interaction).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA = Path(__file__).parent / "schema.sql"
_SCHEMA_VERSION = 1


def init_db(db_path: Path, *, wal: bool = True) -> None:
    """Create the DB file (with parents), apply schema, run light migrations.
    Idempotent — safe to call on every startup."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA.read_text())

        # Light additive migrations (won't hurt fresh DBs)
        _add_column_if_missing(conn, "virtual_positions", "peak_price", "TEXT")

        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        existing = cur.fetchone()[0]
        if existing is None:
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, datetime('now'))",
                (_SCHEMA_VERSION,),
            )
        conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with sane defaults (foreign keys on, row factory).

    Usage:
        with connect(db_path) as conn:
            conn.execute(...)
            conn.commit()   # caller commits explicitly
    """
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit off via BEGIN
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()
