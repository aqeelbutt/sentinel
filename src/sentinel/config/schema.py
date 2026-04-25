"""Pydantic config models — single source of truth for sentinel's configuration.

Loaded by sentinel.core.config.load_config(). A bad value produces a clear
startup error rather than a mid-scan surprise. All Decimal-valued fields are
typed as Decimal so money never round-trips through float.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AppSection(BaseModel):
    mode: Literal["virtual", "paper", "live"] = "virtual"
    timezone: str = "America/New_York"
    initial_virtual_equity: Decimal = Decimal("100000")


class StorageSection(BaseModel):
    db_path: Path = Path("./data/sentinel.db")
    wal: bool = True


class LoggingSection(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_file: Path = Path("./logs/sentinel.jsonl")
    console: bool = True


class WatchlistSection(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    discovery_enabled: bool = True       # also scan the broader DISCOVERY_UNIVERSE
    discovery_max_recs: int = 5          # cap discovery recs per scan to limit noise


class DataSection(BaseModel):
    source: Literal["yfinance"] = "yfinance"
    bar_timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "5m"
    history_days: int = 60


class RegimeSection(BaseModel):
    spy_vwap_window: int = 200
    spy_daily_decline_pct: float = 0.015
    qqq_daily_decline_pct: float = 0.015
    vix_absolute: float = 28.0
    vix_daily_rise_pct: float = 0.15
    block_macro_events: bool = True
    macro_event_cooldown_min: int = 30


class GapAndGoParams(BaseModel):
    min_gap_pct: float = 0.02
    min_rel_volume: float = 2.0
    volume_lookback_days: int = 20


class MeanReversionParams(BaseModel):
    vwap_window: int = 20
    std_dev_threshold: float = 2.5
    rsi_window: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0


class VwapReclaimParams(BaseModel):
    volume_confirmation_mult: float = 1.5


class LiquidityParams(BaseModel):
    min_adv_shares: int = 1_000_000
    max_spread_pct: float = 0.001
    min_price: Decimal = Decimal("5")


class SignalsSection(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["gap_and_go", "mean_reversion", "vwap_reclaim"])
    min_signals_required: int = 2
    gap_and_go: GapAndGoParams = GapAndGoParams()
    mean_reversion: MeanReversionParams = MeanReversionParams()
    vwap_reclaim: VwapReclaimParams = VwapReclaimParams()
    liquidity: LiquidityParams = LiquidityParams()


class NewsSourcesSection(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    refresh_interval_sec: int = 900
    edgar_user_agent: str = "Sentinel sentinel@example.com"


class SentimentSection(BaseModel):
    backend: Literal["stub", "finbert"] = "stub"
    qualify_threshold: float = 0.7
    qualify_min_independent_sources: int = 3
    qualify_max_newest_age_hours: float = 4.0
    decay_breakpoints_hours: tuple[float, float, float] = (1.0, 4.0, 24.0)
    tier_weights: dict[str, float] = Field(
        default_factory=lambda: {"tier1": 1.0, "tier2": 0.5}
    )
    source_tiers: dict[str, str] = Field(default_factory=dict)
    news_sources: NewsSourcesSection = NewsSourcesSection()


class RiskSection(BaseModel):
    risk_per_trade_pct: float = 0.005
    max_concurrent_positions: int = 5
    max_per_position_pct: float = 0.25
    max_deployed_pct: float = 0.40
    daily_loss_limit_pct: float = 0.02
    weekly_loss_limit_pct: float = 0.05
    rolling_window_sessions: int = 5
    max_correlation: float = 0.80
    correlation_lookback_days: int = 30
    consecutive_loss_halt: int = 3
    order_rate_window_sec: int = 300
    order_rate_max: int = 20
    pdt_min_equity: Decimal = Decimal("25000")
    pdt_max_day_trades: int = 3
    pdt_window_business_days: int = 5
    price_sanity_band_pct: float = 0.02
    # Earnings blackout — refuse new entries within this many hours of a
    # symbol's next scheduled earnings release. Earnings = binary risk.
    earnings_blackout_enabled: bool = True
    earnings_blackout_hours: float = 48.0


class VirtualBrokerSection(BaseModel):
    fill_mode: Literal["last_quote", "next_open"] = "last_quote"
    slippage_bps: float = 2.0
    commission_per_share: Decimal = Decimal("0")


class ExitSection(BaseModel):
    """Layered exit logic. Sweep checks in priority order:
       1. profit_take_pct (e.g. +5%) — take the win
       2. trailing stop  — once peak >= activate, trail by trailing_pct
       3. break-even stop — once peak >= breakeven_trigger, exit at entry
       4. hard_stop_pct (e.g. -1%) — initial backstop until break-even arms

       The break-even and trailing stops use the position's `peak_price`
       (highest mark seen since entry), not the current mark. This is what
       guarantees: 'once the trade goes +1% in your favor, you can't lose
       money on it.' Worst case becomes a scratch (~entry).
    """
    breakeven_trigger_pct: float = 0.01
    breakeven_buffer_bps: float = 0          # 0 = sell at exact entry; e.g. 5 = entry * 1.0005
    breakeven_stop_enabled: bool = True

    trailing_stop_activate_pct: float = 0.015
    trailing_stop_pct: float = 0.0075
    trailing_stop_enabled: bool = True

    hard_stop_pct: float = 0.010
    hard_stop_atr_mult: float = 1.5
    auto_stop_enabled: bool = True

    profit_take_pct: float = 0.05
    profit_take_enabled: bool = True

    soft_exit_time: str = "15:45"
    moc_submission_time: str = "15:50"
    hard_liquidation_time: str = "15:55"
    daily_profit_target_pct: float = 0.02


class DashboardSection(BaseModel):
    title: str = "Sentinel"
    refresh_interval_sec: int = 30
    show_debug: bool = False


class Config(BaseModel):
    app: AppSection = AppSection()
    storage: StorageSection = StorageSection()
    logging: LoggingSection = LoggingSection()
    watchlist: WatchlistSection = WatchlistSection()
    data: DataSection = DataSection()
    regime: RegimeSection = RegimeSection()
    signals: SignalsSection = SignalsSection()
    sentiment: SentimentSection = SentimentSection()
    risk: RiskSection = RiskSection()
    virtual_broker: VirtualBrokerSection = VirtualBrokerSection()
    exit: ExitSection = ExitSection()
    dashboard: DashboardSection = DashboardSection()

    @field_validator("app")
    @classmethod
    def _phase1_requires_virtual_mode(cls, v: AppSection) -> AppSection:
        if v.mode != "virtual":
            raise ValueError(
                f"Phase 1 only supports app.mode=virtual; got {v.mode!r}. "
                "Broker integration is deferred — see CLAUDE.md."
            )
        return v
