"""Scanner — runs the Triple-Check across a watchlist and produces Recommendations.

This is the main entry point called by `sentinel scan` and by the dashboard's
'Refresh recommendations' button. One pass is synchronous and bounded by the
size of the watchlist; for a typical 10-20 name list it takes a few seconds.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sentinel.config.schema import Config
from sentinel.core.clock import MarketClock
from sentinel.core.types import Recommendation, SignalFiring
from sentinel.data.macro.regime import MacroRegimeGate
from sentinel.data.market import bar_store, yfinance_feed
from sentinel.data.news.aggregator import fetch_recent_articles
from sentinel.sentiment.weighted import WeightedSentiment, build_backend
from sentinel.storage.repos import decisions, recommendations, watchlist
from sentinel.strategy import confluence
from sentinel.strategy.indicators import atr
from sentinel.strategy.liquidity import check as check_liquidity
from sentinel.strategy.signals.base import SignalContext
from sentinel.strategy.signals.gap_and_go import GapAndGoSignal
from sentinel.strategy.signals.mean_reversion import MeanReversionSignal
from sentinel.strategy.signals.vwap_reclaim import VwapReclaimSignal

log = logging.getLogger(__name__)


def run_scan(cfg: Config, db_path: Path, clock: MarketClock) -> list[Recommendation]:
    """Run one full scan. Returns persisted Recommendations.

    Scans the user's watchlist AND (when enabled) Sentinel's curated
    DISCOVERY_UNIVERSE of liquid US equities — so we can recommend names the
    user hasn't pre-selected. Discovery recs are tagged in the rationale so
    the UI distinguishes 'you asked about this' from 'Sentinel found this for you'.
    """
    watchlist_syms = watchlist.list_all(db_path)
    if not watchlist_syms:
        log.warning("watchlist empty; seeding from config")
        watchlist.replace_all(db_path, cfg.watchlist.symbols)
        watchlist_syms = watchlist.list_all(db_path)

    if cfg.watchlist.discovery_enabled:
        from sentinel.data.universe import DISCOVERY_UNIVERSE
        discovery_set = {s for s in DISCOVERY_UNIVERSE if s not in watchlist_syms}
        symbols = watchlist_syms + sorted(discovery_set)
        log.info("scan universe: %d watchlist + %d discovery = %d total",
                 len(watchlist_syms), len(discovery_set), len(symbols))
    else:
        symbols = watchlist_syms
    watchlist_set = set(watchlist_syms)

    # regime computed once per scan
    regime = MacroRegimeGate(db_path, clock, cfg.regime).evaluate()
    decisions.log(db_path, "regime", payload=asdict(regime), ts=regime.computed_at)

    # sentiment: ingest fresh articles, then score each symbol
    sentiment = WeightedSentiment(db_path, build_backend(cfg.sentiment), clock, cfg.sentiment)
    articles = fetch_recent_articles(symbols, cfg=cfg.sentiment.news_sources)
    if articles:
        sentiment.ingest(articles)
    log.info("scan: fetched %d articles across %d symbols", len(articles), len(symbols))

    # signals
    sig_gap = GapAndGoSignal(cfg.signals.gap_and_go)
    sig_mr = MeanReversionSignal(cfg.signals.mean_reversion)
    sig_vwap = VwapReclaimSignal(cfg.signals.vwap_reclaim)
    active_signals = {
        "gap_and_go": sig_gap,
        "mean_reversion": sig_mr,
        "vwap_reclaim": sig_vwap,
    }

    now = clock.now()
    out: list[Recommendation] = []

    for sym in symbols:
        try:
            intraday = bar_store.get_bars(db_path, sym, cfg.data.bar_timeframe, days=min(cfg.data.history_days, 10))
            daily = bar_store.get_bars(db_path, sym, "1d", days=max(cfg.data.history_days, 60))
            quote = yfinance_feed.latest_quote(sym)
            if not intraday or not daily or not quote:
                log.info("skipping %s (insufficient data)", sym)
                continue

            liq = check_liquidity(cfg.signals.liquidity, daily_bars=daily, quote=quote)
            if not liq.ok:
                decisions.log(db_path, "signal", symbol=sym, payload={"liquidity_block": liq.reason})
                continue

            ctx = SignalContext(symbol=sym, now=now, intraday_5m=intraday, daily=daily)
            firings: list[SignalFiring] = []
            for name in cfg.signals.enabled:
                sig = active_signals.get(name)
                if sig is None:
                    continue
                firing = sig.evaluate(ctx)
                firings.append(firing)
                decisions.log(db_path, "signal", symbol=sym, payload={
                    "name": firing.name.value, "fired": firing.fired,
                    "strength": firing.strength, "inputs": firing.inputs,
                })

            sent = sentiment.score_symbol(sym, at=now)
            decisions.log(db_path, "sentiment", symbol=sym, payload=asdict(sent))

            atr_val = atr(daily, window=14) if len(daily) > 14 else 0.0
            entry_price = Decimal(str(quote.last))
            result = confluence.combine(
                symbol=sym,
                firings=firings,
                sentiment=sent,
                regime=regime,
                entry_price=entry_price,
                atr_value=atr_val or float(entry_price * Decimal("0.01")),
                cfg=cfg,
                now=now,
            )
            fired_names = [f.name.value for f in firings if f.fired]
            if result.recommendation:
                # Tag origin in the rationale: watchlist vs discovery
                origin = "watchlist" if sym in watchlist_set else "discovery"
                rec = result.recommendation
                from dataclasses import replace
                rec = replace(rec, rationale=f"[{origin}]\n{rec.rationale}")
                # Cap discovery recs to avoid noise
                if origin == "discovery":
                    n_disc = sum(1 for r in out if "[discovery]" in r.rationale)
                    if n_disc >= cfg.watchlist.discovery_max_recs:
                        decisions.log(db_path, "risk", symbol=sym,
                                      payload={"reject": f"discovery cap ({cfg.watchlist.discovery_max_recs})"})
                        log.info("scan %s: skipped (discovery cap reached)", sym)
                        continue
                rec_id = recommendations.save(db_path, rec)
                result = type(result)(rec, result.reason)
                decisions.log(db_path, "order", symbol=sym, payload={
                    "recommendation_id": rec_id, "score": result.recommendation.score,
                    "signals": [s.value for s in result.recommendation.signals_fired],
                })
                out.append(result.recommendation)
                log.info(
                    "scan %s: ✅ RECOMMEND score=%.2f signals=[%s] sentiment=%.2f",
                    sym, result.recommendation.score, ",".join(fired_names),
                    sent.score,
                )
            else:
                decisions.log(db_path, "risk", symbol=sym, payload={"reject": result.reason})
                log.info(
                    "scan %s: ❌ skip — %s (signals_fired=[%s], sentiment=%.2f%s)",
                    sym, result.reason, ",".join(fired_names) or "none", sent.score,
                    f", regime_blocks={list(regime.block_reasons)}" if regime.hostile else "",
                )
        except Exception as e:  # noqa: BLE001 — one bad symbol shouldn't sink the scan
            log.exception("scan failed for %s: %s", sym, e)

    log.info("scan complete: %d recommendations across %d symbols", len(out), len(symbols))
    return out
