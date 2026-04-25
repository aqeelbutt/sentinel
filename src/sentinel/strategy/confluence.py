"""Confluence layer — assembles a Recommendation when:
    regime.hostile == False
AND ≥ min_signals_required signals fire
AND (Phase 1) sentiment either qualifies OR is unavailable (backend=stub,
    so no sentiment data — we allow signals + regime to stand alone until
    a real NLP backend is wired up; the UI flags this)

In Phase 2, once FinBERT is on, we require sentiment.qualifies=True to
match the original "Triple-Check" contract. Controlled via
`sentiment.qualify_threshold` (setting it to 0.0 keeps Phase 1 behavior).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from sentinel.config.schema import Config
from sentinel.core.types import (
    Recommendation,
    RecommendationAction,
    RegimeState,
    SignalFiring,
    SignalName,
    SymbolSentiment,
)


@dataclass(frozen=True)
class ConfluenceResult:
    recommendation: Recommendation | None
    reason: str


def combine(
    *,
    symbol: str,
    firings: Sequence[SignalFiring],
    sentiment: SymbolSentiment | None,
    regime: RegimeState,
    entry_price: Decimal,
    atr_value: float,
    cfg: Config,
    now: datetime,
) -> ConfluenceResult:
    if regime.hostile:
        return ConfluenceResult(None, f"regime hostile: {'; '.join(regime.block_reasons)}")

    fired = [f for f in firings if f.fired]
    if len(fired) < cfg.signals.min_signals_required:
        return ConfluenceResult(None, f"only {len(fired)} signals fired (need {cfg.signals.min_signals_required})")

    # Sentiment gate: Phase 1 permissive (stub backend = neutral = never qualifies
    # by spec, but that would block every rec). Strictness is controlled by
    # sentiment_required flag; Phase 2 flips it via config when FinBERT is on.
    sentiment_required = cfg.sentiment.backend != "stub"
    if sentiment_required and (sentiment is None or not sentiment.qualifies):
        return ConfluenceResult(None, "sentiment did not qualify")

    # Score: mean of signal strengths weighted by sentiment confidence if present.
    mean_strength = sum(f.strength for f in fired) / len(fired)
    sentiment_boost = (sentiment.confidence * max(0.0, sentiment.score)) if sentiment else 0.0
    score = min(1.0, 0.7 * mean_strength + 0.3 * sentiment_boost)

    # Suggested stop: max(hard_stop_pct below entry, ATR × hard_stop_atr_mult below entry)
    # — whichever is TIGHTER (higher price for a long) per CLAUDE.md.
    pct_stop = entry_price * (Decimal("1") - Decimal(str(cfg.exit.hard_stop_pct)))
    atr_stop = entry_price - Decimal(str(atr_value * cfg.exit.hard_stop_atr_mult))
    stop = max(pct_stop, atr_stop)

    # TP: target 2× risk as a rule of thumb; overridden later by trailing-stop / profit-take.
    risk_per_share = entry_price - stop
    tp = entry_price + 2 * risk_per_share if risk_per_share > 0 else None

    rationale = _rationale(symbol, fired, sentiment, regime, score, entry_price, stop, tp)

    rec = Recommendation(
        symbol=symbol,
        action=RecommendationAction.BUY,
        score=round(score, 3),
        entry_price_ref=entry_price,
        suggested_stop=stop,
        suggested_take_profit=tp,
        suggested_qty=0,  # filled in by RiskManager.evaluate
        signals_fired=tuple(SignalName(f.name.value) for f in fired),
        sentiment=sentiment,
        regime=regime,
        rationale=rationale,
        created_at=now,
        expires_at=now + timedelta(minutes=30),  # recs stale after 30 min
    )
    return ConfluenceResult(rec, "qualified")


def _rationale(
    symbol: str,
    fired: list[SignalFiring],
    sentiment: SymbolSentiment | None,
    regime: RegimeState,
    score: float,
    entry: Decimal,
    stop: Decimal,
    tp: Decimal | None,
) -> str:
    lines = [f"{symbol}: confluence score {score:.2f}"]
    for f in fired:
        lines.append(f"  • {f.name.value} fired (strength {f.strength:.2f})")
    if sentiment and sentiment.article_count > 0:
        lines.append(
            f"  • sentiment {sentiment.score:+.2f} across {sentiment.article_count} articles / "
            f"{sentiment.independent_sources} independent sources"
        )
    else:
        lines.append("  • sentiment: no qualifying articles (Phase 1 permissive)")
    lines.append(f"  • regime OK (VIX={regime.vix_level:.1f}, SPY intraday={regime.spy_intraday_change_pct:+.2%})")
    lines.append(f"  → entry ~{entry}, stop {stop}, tp {tp if tp else 'trailing'}")
    return "\n".join(lines)
