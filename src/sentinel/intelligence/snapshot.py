"""Builds the structured market snapshot fed to Claude.

Everything the analyst sees comes through here. Keep it tight — tokens cost
money and longer prompts don't necessarily make better analysis. We aim for
a snapshot under 3-4K tokens.

Includes:
  * Watchlist + discovery universe slice
  * Top movers (daily + weekly % change, rel-volume)
  * Macro regime state
  * Upcoming catalysts (next 7 days) on relevant symbols
  * Recent open positions + closed trades (so the analyst doesn't redundantly
    pick what's already in the portfolio)
  * Recent news headlines per top-mover (top 3 each)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sentinel.config.schema import Config
from sentinel.data.market import bar_store
from sentinel.data.universe import DISCOVERY_UNIVERSE
from sentinel.storage.db import connect
from sentinel.storage.repos import catalysts as catalysts_repo
from sentinel.storage.repos import positions, watchlist


def build_market_snapshot(cfg: Config, db_path: Path, max_universe: int = 60) -> dict[str, Any]:
    """Compact snapshot of current market state for the AI analyst."""
    # 1. Universe
    wl = watchlist.list_all(db_path)
    universe = sorted(set(wl) | set(DISCOVERY_UNIVERSE))[:max_universe]

    # 2. Top movers — compute daily + weekly % change + rel-vol
    movers: list[dict[str, Any]] = []
    for sym in universe:
        bars = bar_store.get_bars(db_path, sym, "1d", days=10)
        if len(bars) < 6:
            continue
        last = float(bars[-1].close)
        prev = float(bars[-2].close)
        wk_ago = float(bars[-6].close)
        avg_vol = sum(b.volume for b in bars[-5:]) / 5
        movers.append({
            "symbol": sym,
            "last": round(last, 2),
            "daily_pct": round((last - prev) / prev * 100, 2) if prev > 0 else 0.0,
            "weekly_pct": round((last - wk_ago) / wk_ago * 100, 2) if wk_ago > 0 else 0.0,
            "rel_vol": round(bars[-1].volume / avg_vol, 2) if avg_vol > 0 else 0.0,
            "in_watchlist": sym in wl,
        })
    movers.sort(key=lambda m: -abs(m["daily_pct"]))

    # 3. Macro regime — single SPY/QQQ/VIX read from cached daily bars
    spy = bar_store.get_bars(db_path, "SPY", "1d", days=5)
    qqq = bar_store.get_bars(db_path, "QQQ", "1d", days=5)
    vix = bar_store.get_bars(db_path, "^VIX", "1d", days=5)
    regime = {
        "spy_daily_pct": _last_daily_pct(spy),
        "qqq_daily_pct": _last_daily_pct(qqq),
        "vix_level": float(vix[-1].close) if vix else None,
        "vix_daily_pct": _last_daily_pct(vix),
    }

    # 4. Catalysts in the next 7 days for the universe
    upcoming = catalysts_repo.list_upcoming(db_path, days=7, symbols=universe)
    catalysts = [
        {
            "symbol": e["symbol"],
            "type": e["catalyst_type"],
            "date": e["event_date"],
            "when": e["event_time"] or "",
            "title": e["title"] or "",
        }
        for e in upcoming[:30]
    ]

    # 5. Portfolio context
    open_pos = positions.list_open(db_path)
    portfolio = {
        "open_symbols": [p["symbol"] for p in open_pos],
        "open_count": len(open_pos),
        "max_concurrent": cfg.risk.max_concurrent_positions,
    }

    # 6. Recent news headlines per top mover
    headlines = _recent_headlines_per_symbol(db_path, [m["symbol"] for m in movers[:10]])

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "top_movers": movers[:20],
        "catalysts_next_7d": catalysts,
        "portfolio": portfolio,
        "recent_headlines": headlines,
        "config_hints": {
            "min_signals_required": cfg.signals.min_signals_required,
            "earnings_blackout_hours": cfg.risk.earnings_blackout_hours,
            "max_per_position_pct": cfg.risk.max_per_position_pct,
            "risk_per_trade_pct": cfg.risk.risk_per_trade_pct,
        },
    }


def _last_daily_pct(bars: list) -> float | None:
    if len(bars) < 2:
        return None
    last = float(bars[-1].close)
    prev = float(bars[-2].close)
    return round((last - prev) / prev * 100, 2) if prev > 0 else None


def _recent_headlines_per_symbol(db_path: Path, symbols: list[str], hours: int = 24, per_sym: int = 3) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out: dict[str, list[dict]] = {}
    with connect(db_path) as conn:
        for sym in symbols:
            rows = conn.execute(
                "SELECT source, title, published_at, raw_score FROM sentiment_cache "
                "WHERE published_at >= ? AND symbols LIKE ? "
                "ORDER BY published_at DESC LIMIT ?",
                (cutoff, f'%"{sym}"%', per_sym),
            ).fetchall()
            if rows:
                out[sym] = [
                    {
                        "source": r["source"], "title": r["title"][:200],
                        "published_at": r["published_at"][:16],
                        "sentiment": round(r["raw_score"], 2) if r["raw_score"] is not None else None,
                    }
                    for r in rows
                ]
    return out
