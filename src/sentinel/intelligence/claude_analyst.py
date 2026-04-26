"""Claude analyst — sends a structured market snapshot to the Anthropic API
and parses the JSON list of AIRecommendations.

Defaults to Claude Sonnet 4.6 (best price/quality for this kind of synthesis).
Switch to Opus for higher-stakes analysis. Haiku is too thin for multi-factor
reasoning here.

Cost approximation (per scan):
  Sonnet 4.6  ~$0.005-0.020  recommended default
  Opus 4.7    ~$0.020-0.080  for higher conviction work
  Haiku 4.5   ~$0.001-0.004  not recommended; misses cross-factor reasoning
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from sentinel.config.schema import Config
from sentinel.intelligence.snapshot import build_market_snapshot
from sentinel.intelligence.types import AIRecommendation
from sentinel.ops.keychain import get_secret
from sentinel.storage.repos import ai_recommendations as ai_repo

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_RECS = 5

SYSTEM_PROMPT = """You are an expert quantitative day-trading analyst working for a single \
solo trader using Sentinel, a confluence-based recommendation engine. Your job is to read a \
structured market snapshot and produce 3-5 high-conviction, evidence-grounded trade ideas \
for THE NEXT TRADING SESSION.

HARD RULES (non-negotiable):
1. Output ONLY valid JSON matching the schema below. No prose before or after.
2. Recommend ONLY US-listed equities/ETFs from the snapshot's `top_movers` or symbols \
appearing in `recent_headlines`. Do NOT invent symbols.
3. NEVER recommend a symbol that has an `earnings` event in `catalysts_next_7d` within 48 hours.
4. NEVER recommend a symbol that's already in `portfolio.open_symbols` (no doubling up).
5. Every thesis MUST cite at least one specific data point from the snapshot — quote it.
6. If the regime is hostile (VIX > 28 or major index down >1.5%), output an EMPTY recommendations \
array with `"market_assessment"` explaining why no entries.
7. Conviction should be `high` only when 3+ data points align (technical + fundamental + sentiment). \
`medium` when 2 align. `low` for single-factor leans.
8. Time horizon: `intraday` for momentum/volume plays, `swing` for 1-5 day catalyst plays.
9. Each thesis should be 1-3 sentences, tight and specific. NO hedging language. NO disclaimers.

OUTPUT SCHEMA (return EXACTLY this shape):
{
  "market_assessment": "1-2 sentence read on the day's tape",
  "recommendations": [
    {
      "symbol": "TICKER",
      "thesis": "Specific 1-3 sentence trade idea citing snapshot data points.",
      "catalysts_cited": ["catalyst phrase 1", "catalyst phrase 2"],
      "conviction": "low|medium|high",
      "risk_factors": ["specific risk 1", "specific risk 2"],
      "time_horizon": "intraday|swing|position",
      "inputs_used": ["e.g. top_movers.NVDA daily +4.32%", "e.g. catalysts_next_7d none for NVDA"]
    }
  ]
}"""

USER_PROMPT_TEMPLATE = """Here is the current market snapshot. Produce up to {max_recs} \
trade recommendations for the next session, following the rules in your system prompt.

```json
{snapshot_json}
```"""


class AIAnalystError(RuntimeError):
    pass


def run_analyst(
    cfg: Config,
    db_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    max_recs: int = MAX_RECS,
) -> list[AIRecommendation]:
    """Build snapshot, call Claude, persist parsed recommendations. Idempotent
    on inputs — if the same snapshot was just analyzed, returns those recs
    instead of re-calling the API."""
    api_key = get_secret("anthropic")
    if not api_key:
        raise AIAnalystError(
            "Anthropic API key not set. Run: sentinel keychain set anthropic <key>"
        )

    snapshot = build_market_snapshot(cfg, db_path)
    snapshot_json = json.dumps(snapshot, indent=2, default=str)
    inputs_hash = hashlib.sha256(snapshot_json.encode()).hexdigest()[:16]

    # Idempotency: if the snapshot is unchanged from last run (within 5min cache),
    # return the cached recs to avoid burning API spend.
    cached = ai_repo.list_by_inputs_hash(db_path, inputs_hash, max_age_minutes=5)
    if cached:
        log.info("ai analyst: snapshot unchanged in last 5min, reusing %d cached recs", len(cached))
        return cached

    client = Anthropic(api_key=api_key)
    log.info("ai analyst: calling %s on snapshot %s (~%d chars)", model, inputs_hash, len(snapshot_json))
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,    # was 2000 — got truncated mid-string on 5-rec runs
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                    max_recs=max_recs,
                    snapshot_json=snapshot_json,
                )},
            ],
        )
    except Exception as e:  # noqa: BLE001 — anthropic errors vary
        raise AIAnalystError(f"Anthropic API call failed: {e}") from e

    raw_text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    cost_usd = _estimate_cost(model, resp.usage.input_tokens, resp.usage.output_tokens)
    stop = getattr(resp, "stop_reason", "?")
    if stop == "max_tokens":
        log.warning("ai analyst: response hit max_tokens — last rec may be incomplete; recovering parseable items")
    log.info("ai analyst: %d input + %d output tokens (~$%.4f) stop=%s",
             resp.usage.input_tokens, resp.usage.output_tokens, cost_usd or 0.0, stop)

    parsed = _parse_response(raw_text)
    now = datetime.now(timezone.utc)
    recs = []
    for item in parsed.get("recommendations", []):
        rec = _build_rec(item, inputs_hash=inputs_hash, model=model,
                         cost_usd=cost_usd, raw_text=raw_text, now=now)
        if rec:
            ai_repo.save(db_path, rec)
            recs.append(rec)
    log.info("ai analyst: parsed %d recommendations", len(recs))
    return recs


# ---------- helpers ----------

def _parse_response(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction — strips ``` fences and recovers partial
    output when the model hit max_tokens mid-array (so we don't lose the
    first N completed recommendations because the (N+1)th was cut off)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    s = s.strip()

    # Path 1: try the whole thing as-is
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Path 2: find the outer object and try again
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            pass

    # Path 3: partial recovery — cut at the last "}, " inside recommendations
    # array and synthesize a closing "]}"
    recovered = _recover_truncated(s)
    if recovered is not None:
        log.info("ai analyst: recovered %d complete recommendations from truncated JSON",
                 len(recovered.get("recommendations", [])))
        return recovered

    raise AIAnalystError(f"Could not parse model JSON. Raw output (first 500 chars):\n{text[:500]}")


def _recover_truncated(s: str) -> dict[str, Any] | None:
    """Try to recover the largest prefix of `s` that's valid JSON. Strategy:
    locate the last well-formed `},` boundary inside the `recommendations`
    array and close the array + outer object after that."""
    rec_idx = s.find('"recommendations"')
    if rec_idx < 0:
        return None
    # Find the start of the array
    arr_start = s.find("[", rec_idx)
    if arr_start < 0:
        return None
    # Walk forward, tracking brace depth; collect end positions of complete
    # recommendation objects (depth returns to 1 after a closing '}').
    depth = 1
    in_str = False
    escape = False
    last_complete_end = -1
    for k in range(arr_start + 1, len(s)):
        c = s[k]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 1:
                last_complete_end = k
    if last_complete_end < 0:
        return None
    truncated = s[:last_complete_end + 1] + "\n  ]\n}"
    try:
        return json.loads(truncated)
    except json.JSONDecodeError:
        return None


def _build_rec(
    item: dict, *, inputs_hash: str, model: str, cost_usd: float | None,
    raw_text: str, now: datetime,
) -> AIRecommendation | None:
    sym = (item.get("symbol") or "").upper().strip()
    if not sym:
        return None
    return AIRecommendation(
        id=str(uuid.uuid4()),
        symbol=sym,
        thesis=item.get("thesis", "").strip()[:1000],
        catalysts_cited=tuple(item.get("catalysts_cited", []))[:10],
        conviction=item.get("conviction", "low"),
        risk_factors=tuple(item.get("risk_factors", []))[:10],
        time_horizon=item.get("time_horizon", "swing"),
        inputs_used=tuple(item.get("inputs_used", []))[:15],
        inputs_hash=inputs_hash,
        model=model,
        cost_usd=cost_usd,
        raw_response=raw_text[:8000],
        created_at=now,
    )


# Pricing approximations as of 2026 (USD per million tokens). Keep updated.
_PRICING = {
    "claude-opus-4-7":     (15.0, 75.0),
    "claude-sonnet-4-6":   (3.0, 15.0),
    "claude-haiku-4-5":    (0.80, 4.00),
}


def _estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float | None:
    # Match by prefix so versioned ids (claude-opus-4-7-20260101) still resolve
    for key, (in_p, out_p) in _PRICING.items():
        if model.startswith(key):
            return round((in_tokens * in_p + out_tokens * out_p) / 1_000_000, 6)
    return None
