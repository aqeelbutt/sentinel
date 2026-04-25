"""yfinance wrapper. Synchronous (yfinance is synchronous); we wrap calls
in `asyncio.to_thread` at the pipeline boundary if needed.

Notes:
  - yfinance returns naive timestamps in the exchange's local time for
    intraday data — we convert to UTC explicitly here.
  - 1m bars only available for last 7 days; 5m for last 60 days; daily for
    decades. The default config uses 5m which covers our backtest needs.
  - Volume in extended hours is included in yfinance bars; we don't try to
    distinguish RTH vs ETH volume in Phase 1.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

import pandas as pd
import yfinance as yf

from sentinel.core.types import Bar, Quote

log = logging.getLogger(__name__)


_TIMEFRAME_TO_YF = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "1d": "1d",
}


def fetch_bars(
    symbol: str,
    timeframe: str,
    *,
    days: int = 60,
) -> list[Bar]:
    """Pull recent bars for one symbol. Returns [] on failure (logged)."""
    interval = _TIMEFRAME_TO_YF.get(timeframe)
    if interval is None:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    try:
        period = _period_for_days(days, timeframe)
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    except Exception as e:  # noqa: BLE001 — yfinance throws everything
        log.warning("yfinance fetch failed", extra={"symbol": symbol, "error": str(e)})
        return []
    if df.empty:
        return []
    return _df_to_bars(symbol, timeframe, df)


def fetch_bars_many(
    symbols: Iterable[str],
    timeframe: str,
    *,
    days: int = 60,
) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    for s in symbols:
        out[s] = fetch_bars(s, timeframe, days=days)
    return out


def latest_quote(symbol: str) -> Quote | None:
    """Approximate quote from most recent 1m bar — yfinance doesn't expose true NBBO.
    Spread is fabricated as 1bp for liquid names; consumers must treat this as a
    rough mark for virtual fills, not a real spread."""
    try:
        df = yf.Ticker(symbol).history(period="1d", interval="1m", auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        log.warning("yfinance quote failed", extra={"symbol": symbol, "error": str(e)})
        return None
    if df.empty:
        return None
    last_row = df.iloc[-1]
    last_ts = df.index[-1].to_pydatetime()
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    last_price = Decimal(str(round(float(last_row["Close"]), 4)))
    half_spread = last_price * Decimal("0.00005")  # ~1bp synthetic
    return Quote(
        symbol=symbol,
        ts=last_ts.astimezone(timezone.utc),
        bid=last_price - half_spread,
        ask=last_price + half_spread,
        last=last_price,
    )


def _period_for_days(days: int, timeframe: str) -> str:
    # yfinance accepts {1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max}
    if timeframe == "1m":
        return f"{min(days, 7)}d"
    if timeframe in ("5m", "15m", "1h"):
        return f"{min(days, 60)}d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    if days <= 1825:
        return "5y"
    if days <= 3650:
        return "10y"
    return "max"


def _df_to_bars(symbol: str, timeframe: str, df: pd.DataFrame) -> list[Bar]:
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        ts_dt: datetime = ts.to_pydatetime()
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        ts_dt = ts_dt.astimezone(timezone.utc)
        try:
            bars.append(
                Bar(
                    symbol=symbol,
                    ts=ts_dt,
                    open=Decimal(str(round(float(row["Open"]), 4))),
                    high=Decimal(str(round(float(row["High"]), 4))),
                    low=Decimal(str(round(float(row["Low"]), 4))),
                    close=Decimal(str(round(float(row["Close"]), 4))),
                    volume=int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
                    timeframe=timeframe,
                )
            )
        except (ValueError, TypeError):
            continue
    return bars
