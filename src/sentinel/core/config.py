"""Config loader. One function, one responsibility: YAML → validated Config.

YAML lookup order:
  1. explicit `path` argument
  2. `SENTINEL_CONFIG` env var
  3. `<repo_root>/config/default.yaml` (works in editable installs / running from clone)
  4. `<package>/_data/default.yaml` (would be used if YAML is bundled in the wheel)
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from sentinel.config.schema import Config

# Repo root is .../sentinel/   (parents[3] from src/sentinel/core/config.py)
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "default.yaml"


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate sentinel config."""
    resolved = Path(path or os.environ.get("SENTINEL_CONFIG") or DEFAULT_CONFIG_PATH)
    if not resolved.exists():
        raise FileNotFoundError(
            f"Config file not found at {resolved}. "
            "Set SENTINEL_CONFIG, pass a path, or run from the repo root with config/default.yaml present."
        )
    with resolved.open() as f:
        raw = yaml.safe_load(f) or {}
    return Config.model_validate(raw)
