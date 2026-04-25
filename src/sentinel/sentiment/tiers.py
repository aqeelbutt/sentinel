"""Source → tier mapping, plus canonical domain dedupe for 'independent sources'.

Why canonical dedupe matters: Reuters publishes a story; Yahoo Finance re-posts
the Reuters feed as its own news item; Benzinga writes a quick summary of the
Reuters story. Those are NOT three independent sources — they're one story in
three places. We map each article's source/URL to a canonical origin so the
'≥ 3 independent sources' gate measures actual editorial independence.
"""
from __future__ import annotations

from urllib.parse import urlparse


# Canonical origin for known syndication paths. If a Yahoo-Finance article's
# inner link points to a Reuters-byline story we count it as 'reuters', not 'yahoo'.
_SYNDICATION_HINTS = {
    "reuters": "reuters",
    "bloomberg": "bloomberg",
    "wsj.com": "wsj",
    "cnbc.com": "cnbc",
    "marketwatch.com": "marketwatch",
    "benzinga.com": "benzinga",
    "seekingalpha.com": "seeking_alpha",
    "ft.com": "ft",
    "barrons.com": "barrons",
    "sec.gov": "sec_edgar",
}


def tier_weight(source: str, tier_weights: dict[str, float], source_tiers: dict[str, str]) -> float:
    tier = source_tiers.get(source)
    if tier is None:
        return 0.0
    return tier_weights.get(tier, 0.0)


def canonical_origin(source: str, url: str | None = None) -> str:
    """Collapse syndicated copies to their canonical origin.

    The `source` column in sentiment_cache is already the fetcher-assigned
    canonical key (see data/news/aggregator.py _canonical_source), so in
    practice this is idempotent. Kept as a guard in case a raw URL ever
    leaks through.
    """
    if url:
        host = urlparse(url).hostname or ""
        for needle, canon in _SYNDICATION_HINTS.items():
            if needle in host:
                return canon
    return source
