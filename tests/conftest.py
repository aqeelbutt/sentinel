"""Test fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.storage.db import init_db


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db, wal=False)
    return db


@pytest.fixture
def cfg(tmp_db: Path):
    """Minimal Config for tests, pointed at the temp DB."""
    from sentinel.config.schema import Config, AppSection, StorageSection
    from decimal import Decimal
    cfg = Config()
    cfg.storage = StorageSection(db_path=tmp_db, wal=False)
    cfg.app = AppSection(initial_virtual_equity=Decimal("100000"))
    return cfg
