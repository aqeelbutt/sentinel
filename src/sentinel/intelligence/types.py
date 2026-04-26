"""AIRecommendation domain type — structured output from the Claude analyst."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Conviction = Literal["low", "medium", "high"]
TimeHorizon = Literal["intraday", "swing", "position"]


@dataclass(frozen=True)
class AIRecommendation:
    id: str
    symbol: str
    thesis: str                          # 1-3 sentences, structured analysis
    catalysts_cited: tuple[str, ...]     # e.g. ("Q1 earnings beat", "AI capex tailwind")
    conviction: Conviction
    risk_factors: tuple[str, ...]
    time_horizon: TimeHorizon
    inputs_used: tuple[str, ...]         # data points the model says it used
    inputs_hash: str
    model: str
    cost_usd: float | None
    raw_response: str
    created_at: datetime
