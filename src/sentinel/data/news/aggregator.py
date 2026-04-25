"""News aggregator. Pulls from trusted financial sources only.

Sources (all free, no key required):
  * SEC EDGAR       — official company filings (8-K, 10-Q, 10-K, etc.)
                      Requires a User-Agent header; set `sentiment.news_sources.edgar_user_agent`
                      to your real email before enabling EDGAR in production.
  * Yahoo Finance   — news feed via yfinance.Ticker(X).news (mix of Reuters, AP, Bloomberg
                      teasers, Benzinga, etc. — we map to our tier system by domain)
  * Reuters RSS     — https://feeds.reuters.com/reuters/businessNews
  * CNBC RSS        — https://www.cnbc.com/id/15839069/device/rss/rss.html (Top News)
  * MarketWatch RSS — https://feeds.marketwatch.com/marketwatch/topstories/

Social media (Reddit, StockTwits, X) is intentionally excluded — per the project
decision to rely only on editorially-curated financial sources.

Each fetcher returns [] on failure (logged); the aggregator never raises. A dead
RSS feed should degrade the signal, not crash the scanner.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import feedparser
import httpx
import yfinance as yf

from sentinel.config.schema import NewsSourcesSection

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Article:
    id: str
    source: str
    url: str
    published_at: datetime
    title: str
    body: str
    symbols: tuple[str, ...]


# ---------------- source → canonical key ---------------------------------

_DOMAIN_TO_SOURCE = {
    "reuters.com": "reuters",
    "feeds.reuters.com": "reuters",
    "bloomberg.com": "bloomberg",
    "wsj.com": "wsj",
    "ft.com": "ft",
    "barrons.com": "barrons",
    "benzinga.com": "benzinga",
    "seekingalpha.com": "seeking_alpha",
    "cnbc.com": "cnbc",
    "marketwatch.com": "marketwatch",
    "finance.yahoo.com": "yahoo_finance",
    "yahoo.com": "yahoo_finance",
    "sec.gov": "sec_edgar",
}


# Normalize upstream source names (from NewsAPI `source.id/name` and Finnhub
# `source`) to our canonical tier keys. Keep both pretty and ugly variants
# because upstream capitalizes inconsistently.
_NAME_TO_CANONICAL = {
    "reuters": "reuters",
    "bloomberg": "bloomberg",
    "wsj": "wsj", "wall street journal": "wsj", "the wall street journal": "wsj",
    "ft": "ft", "financial times": "ft",
    "barrons": "barrons", "barron's": "barrons", "barrons.com": "barrons",
    "cnbc": "cnbc",
    "marketwatch": "marketwatch", "market watch": "marketwatch",
    "yahoo": "yahoo_finance", "yahoo finance": "yahoo_finance", "yahoo_finance": "yahoo_finance",
    "benzinga": "benzinga",
    "seekingalpha": "seeking_alpha", "seeking alpha": "seeking_alpha", "seeking_alpha": "seeking_alpha",
    "sec edgar": "sec_edgar", "sec_edgar": "sec_edgar",
    "finnhub": "finnhub",
    "newsapi": "newsapi",
    # low-quality aggregators — we keep them labeled but they get 0 weight
    # because they're not in source_tiers.
    "chartmill": "chartmill",
    "investorplace": "investorplace",
    "motley fool": "motley_fool", "the motley fool": "motley_fool",
    "zacks": "zacks", "zacks investment research": "zacks",
}


def _canonical_source(url: str, fallback: str) -> str:
    for domain, key in _DOMAIN_TO_SOURCE.items():
        if domain in url:
            return key
    if fallback:
        normalized = _NAME_TO_CANONICAL.get(fallback.strip().lower())
        if normalized:
            return normalized
        return fallback.strip().lower().replace(" ", "_")
    return "unknown"


def _article_id(url: str, title: str) -> str:
    h = hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()
    return h[:24]


# ---------------- individual fetchers ------------------------------------

def fetch_yahoo_news(symbol: str) -> list[Article]:
    """yfinance exposes a .news list per ticker (Yahoo Finance aggregation)."""
    try:
        raw = yf.Ticker(symbol).news or []
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo news fetch failed", extra={"symbol": symbol, "error": str(e)})
        return []
    out: list[Article] = []
    for item in raw:
        # yfinance schema varies over time; defensive access
        link = item.get("link") or item.get("url") or ""
        title = item.get("title") or ""
        if not link or not title:
            continue
        published_epoch = item.get("providerPublishTime") or item.get("pubDate")
        if published_epoch is None:
            continue
        try:
            published = datetime.fromtimestamp(int(published_epoch), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        source = _canonical_source(link, fallback="yahoo_finance")
        out.append(
            Article(
                id=_article_id(link, title),
                source=source,
                url=link,
                published_at=published,
                title=title,
                body=item.get("summary") or title,
                symbols=(symbol,),
            )
        )
    return out


def fetch_rss(url: str, source_key: str, *, symbols_hint: Iterable[str] = ()) -> list[Article]:
    """Generic RSS fetcher. `source_key` is the canonical tier key (reuters, cnbc, marketwatch)."""
    try:
        parsed = feedparser.parse(url)
    except Exception as e:  # noqa: BLE001
        log.warning("rss fetch failed for %s: %s", url, e)
        return []
    if parsed.bozo and not parsed.entries:
        log.warning("rss parse error for %s: %s", url, parsed.bozo_exception)
        return []
    out: list[Article] = []
    hint_set = {s.upper() for s in symbols_hint}
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        if not link or not title:
            continue
        published = _parse_feed_ts(entry)
        if published is None:
            continue
        body = entry.get("summary") or title
        symbols = tuple(_extract_tickers(f"{title} {body}", hint_set))
        if not symbols:
            continue  # business-wire RSS items without a detectable ticker aren't useful to us
        out.append(
            Article(
                id=_article_id(link, title),
                source=source_key,
                url=link,
                published_at=published,
                title=title,
                body=body,
                symbols=symbols,
            )
        )
    return out


def fetch_finnhub(symbols: Iterable[str], *, api_key: str, lookback_hours: int = 24) -> list[Article]:
    """Per-symbol company-news from Finnhub.

    Finnhub free tier: 60 req/minute, real-time US company news. Perfect fit
    because it's symbol-scoped (no keyword matching needed) and the source
    field maps cleanly to our tiers (Reuters, Bloomberg, CNBC, MarketWatch,
    Benzinga, etc.).

    Budget: 1 request per symbol per scan. With a 13-symbol watchlist and
    5-minute scan interval = 156 req/hour — well under the 3600/hr ceiling.
    """
    if not api_key:
        log.warning("finnhub enabled but no API key — set via `sentinel keychain set finnhub <key>`")
        return []
    from datetime import date
    today = date.today().isoformat()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=max(1, lookback_hours // 24 + 1))).date().isoformat()
    cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
    out: list[Article] = []
    # Token via header, NOT query param — otherwise httpx's INFO-level URL
    # logging would leak the key into logs/sentinel.jsonl.
    headers = {"X-Finnhub-Token": api_key}
    with httpx.Client(timeout=10.0, headers=headers) as client:
        for sym in symbols:
            try:
                r = client.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": sym.upper(), "from": week_ago, "to": today},
                )
                if r.status_code == 429:
                    log.warning("finnhub rate-limited (429); skipping remaining symbols for this cycle")
                    break
                r.raise_for_status()
                items = r.json() or []
            except Exception as e:  # noqa: BLE001
                log.warning("finnhub fetch failed for %s: %s", sym, e)
                continue
            for item in items:
                ts_epoch = item.get("datetime")
                url = item.get("url") or ""
                headline = item.get("headline") or ""
                if not (ts_epoch and url and headline) or ts_epoch < cutoff:
                    continue
                published = datetime.fromtimestamp(int(ts_epoch), tz=timezone.utc)
                source = _canonical_source(url, fallback=item.get("source") or "finnhub")
                out.append(Article(
                    id=_article_id(url, headline),
                    source=source, url=url, published_at=published,
                    title=headline, body=item.get("summary") or headline,
                    symbols=(sym.upper(),),
                ))
    return out


def fetch_newsapi(symbols: Iterable[str], *, api_key: str, lookback_hours: int = 24) -> list[Article]:
    """NewsAPI `/v2/everything`. Batches symbols into OR-queries to minimize request count.

    WARNING: NewsAPI's free 'Developer' tier delays content by 24 hours. Articles
    returned will typically be too old to qualify under our 4-hour recency gate.
    Still useful for filling in context / backfill. Upgrade tier for real-time use.

    Budget: 5 symbols per request; 100 req/day free. At 5-min scans × 6.5h × 3
    batches for 13 symbols = ~230/day — over quota. Cap at ~10 batches/hour;
    on 429, drop and wait for the next scan.
    """
    if not api_key:
        log.warning("newsapi enabled but no API key — set via `sentinel keychain set newsapi <key>`")
        return []
    symbols = list(symbols)
    if not symbols:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    since = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    out: list[Article] = []
    trusted_domains = (
        "reuters.com,bloomberg.com,wsj.com,ft.com,barrons.com,"
        "cnbc.com,marketwatch.com,benzinga.com,seekingalpha.com"
    )
    with httpx.Client(timeout=10.0) as client:
        for batch in _chunks(symbols, 5):
            q = " OR ".join(f'"{s}"' for s in batch)
            try:
                r = client.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": q, "from": since, "language": "en",
                        "sortBy": "publishedAt", "pageSize": 50,
                        "domains": trusted_domains,
                    },
                    headers={"X-Api-Key": api_key},
                )
                if r.status_code == 429:
                    log.warning("newsapi rate-limited (429); stopping further batches")
                    break
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                log.warning("newsapi fetch failed for batch %s: %s", batch, e)
                continue
            if data.get("status") != "ok":
                log.warning("newsapi non-ok status: %s", data.get("message", data.get("status")))
                continue
            hint = {s.upper() for s in batch}
            for item in data.get("articles", []):
                url = item.get("url") or ""
                title = item.get("title") or ""
                published_str = item.get("publishedAt") or ""
                if not (url and title and published_str):
                    continue
                try:
                    published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                text = f"{title} {item.get('description') or ''}"
                matched = tuple(_extract_tickers(text, hint))
                if not matched:
                    continue
                source = _canonical_source(url, fallback=(item.get("source") or {}).get("id") or "newsapi")
                out.append(Article(
                    id=_article_id(url, title),
                    source=source, url=url, published_at=published,
                    title=title, body=item.get("description") or title,
                    symbols=matched,
                ))
    return out


def _chunks(xs: list, n: int) -> list[list]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def fetch_sec_edgar(symbols: Iterable[str], *, user_agent: str, lookback_hours: int = 24) -> list[Article]:
    """Pull recent 8-K / 10-Q / 10-K filings for the given tickers.

    Uses EDGAR's JSON submission API. SEC requires a non-bot User-Agent with
    a real contact email; do not leave the default. Rate-limited to ~10 req/sec
    by SEC — we request one ticker at a time with a small sleep via httpx.
    """
    # Detect placeholder / obviously-invalid user agents. SEC will reject anything
    # without a real contact email; they also ban User-Agents that look bot-like.
    ua_lower = user_agent.lower().strip()
    looks_placeholder = (
        "example.com" in ua_lower
        or "@example" in ua_lower
        or "your-" in ua_lower
        or "@" not in ua_lower   # no email at all
        or ua_lower == "sentinel"
    )
    if looks_placeholder:
        log.warning(
            "SEC EDGAR user-agent looks like a placeholder (%r) — skipping EDGAR. "
            "Set sentiment.news_sources.edgar_user_agent to include your real email.",
            user_agent,
        )
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
    out: list[Article] = []
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=10.0) as client:
        # We need CIK per ticker. SEC provides a ticker→CIK JSON map.
        try:
            resp = client.get("https://www.sec.gov/files/company_tickers.json")
            resp.raise_for_status()
            cik_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in resp.json().values()}
        except Exception as e:  # noqa: BLE001
            log.warning("sec ticker map fetch failed", extra={"error": str(e)})
            return []
        for sym in symbols:
            cik = cik_map.get(sym.upper())
            if not cik:
                continue
            try:
                r = client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                r.raise_for_status()
                subs = r.json().get("filings", {}).get("recent", {})
            except Exception as e:  # noqa: BLE001
                log.warning("sec submissions fetch failed", extra={"symbol": sym, "error": str(e)})
                continue
            forms = subs.get("form", [])
            dates = subs.get("filingDate", [])
            acc_nums = subs.get("accessionNumber", [])
            primary_docs = subs.get("primaryDocument", [])
            for form, fdate, acc, doc in zip(forms, dates, acc_nums, primary_docs):
                if form not in ("8-K", "10-Q", "10-K", "6-K", "S-1"):
                    continue
                try:
                    filed = datetime.fromisoformat(fdate).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if filed.timestamp() < cutoff:
                    continue
                acc_nodash = acc.replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
                title = f"{sym} {form} filing ({fdate})"
                out.append(
                    Article(
                        id=_article_id(url, title),
                        source="sec_edgar",
                        url=url,
                        published_at=filed,
                        title=title,
                        body=f"{sym} filed {form} with the SEC on {fdate}.",
                        symbols=(sym,),
                    )
                )
    return out


# ---------------- orchestrator -------------------------------------------

# Reuters intentionally absent — they shut down public RSS in 2020 (feeds.reuters.com
# no longer resolves). For Reuters coverage now, content shows up via Yahoo Finance
# news (which syndicates Reuters bylines) — the canonical-source dedupe in
# sentiment/tiers.py credits those to 'reuters' for the Tier-1 weight.
_RSS_FEEDS = {
    "cnbc_rss": ("https://www.cnbc.com/id/15839069/device/rss/rss.html", "cnbc"),
    "cnbc_markets_rss": ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "cnbc"),
    "marketwatch_rss": ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "marketwatch"),
    "yahoo_rss": ("https://finance.yahoo.com/news/rssindex", "yahoo_finance"),
}


def fetch_recent_articles(
    symbols: Iterable[str],
    *,
    cfg: NewsSourcesSection,
) -> list[Article]:
    """Best-effort pull across every configured source. Returns a flat list,
    deduped by (source, article_id). Each source failure is logged and skipped."""
    symbols = list(dict.fromkeys(s.upper() for s in symbols))  # uniq, order-preserving
    collected: dict[tuple[str, str], Article] = {}

    from sentinel.ops.keychain import get_secret

    for source in cfg.enabled:
        try:
            if source == "yahoo_finance":
                for s in symbols:
                    for a in fetch_yahoo_news(s):
                        collected[(a.source, a.id)] = a
            elif source == "sec_edgar":
                for a in fetch_sec_edgar(symbols, user_agent=cfg.edgar_user_agent):
                    collected[(a.source, a.id)] = a
            elif source == "finnhub":
                key = get_secret("finnhub")
                for a in fetch_finnhub(symbols, api_key=key or ""):
                    collected[(a.source, a.id)] = a
            elif source == "newsapi":
                key = get_secret("newsapi")
                for a in fetch_newsapi(symbols, api_key=key or ""):
                    collected[(a.source, a.id)] = a
            elif source in _RSS_FEEDS:
                url, src_key = _RSS_FEEDS[source]
                for a in fetch_rss(url, src_key, symbols_hint=symbols):
                    collected[(a.source, a.id)] = a
            else:
                log.warning("unknown news source: %s", source)
        except Exception as e:  # noqa: BLE001 — never let one source kill the run
            log.exception("news source failed: source=%s error=%s", source, e)
    return list(collected.values())


# ---------------- helpers -------------------------------------------------

_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def _extract_tickers(text: str, hint: set[str]) -> list[str]:
    """Find tickers in free text. We only return ones in the caller's `hint`
    set — this avoids common-word false positives like 'CEO' or 'USA'. If the
    caller provides no hint we return [] (RSS item is dropped)."""
    if not hint:
        return []
    found = {m.group(1) for m in _TICKER_RE.finditer(text)}
    return sorted(found & hint)


def _parse_feed_ts(entry: dict) -> datetime | None:
    import time
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None
