"""Data sources status — confirms keys are wired and news is flowing.

One quick page to answer: 'Is Finnhub/NewsAPI actually working?' without
squinting at logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts
from sentinel.dashboard.state import get_config
from sentinel.ops.keychain import get_secret
from sentinel.storage.db import connect
from sentinel.storage.repos import decisions


def render() -> None:
    cfg = get_config()
    st.title("Data sources")
    st.caption("Verify keys are wired and news is actually flowing. Refresh the page after a scan to update counts.")

    # ---------- API key status ----------
    st.subheader("API keys")
    key_rows = []
    for name in ("finnhub", "newsapi"):
        enabled = name in cfg.sentiment.news_sources.enabled
        secret = get_secret(name)
        if secret:
            preview = secret[:4] + "…" + secret[-4:] if len(secret) > 8 else "***"
            key_state = f"✅ set ({preview})"
        else:
            key_state = "❌ not set"
        key_rows.append({
            "Source": name,
            "Enabled in config": "yes" if enabled else "no",
            "Key in Keychain": key_state,
            "Status": (
                "active" if (enabled and secret)
                else "inactive (enabled but no key)" if enabled
                else "disabled"
            ),
        })
    st.dataframe(pd.DataFrame(key_rows), hide_index=True, width="stretch")

    st.caption(
        "To update: `sentinel keychain set <name> <key>` in a terminal, then restart the dashboard."
    )

    # ---------- article counts by source ----------
    st.subheader("Articles ingested (by source)")

    now = datetime.now(timezone.utc)
    cutoffs = {
        "1h": (now - timedelta(hours=1)).isoformat(),
        "4h": (now - timedelta(hours=4)).isoformat(),
        "24h": (now - timedelta(hours=24)).isoformat(),
    }
    with connect(cfg.storage.db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              source,
              SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) AS c1h,
              SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) AS c4h,
              SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) AS c24h,
              COUNT(*) AS total,
              MAX(published_at) AS newest
            FROM sentiment_cache
            GROUP BY source
            ORDER BY c24h DESC, total DESC
            """,
            (cutoffs["1h"], cutoffs["4h"], cutoffs["24h"]),
        ).fetchall()

    if not rows:
        st.info(
            "No articles cached yet. Click **Run scan now** on the Recommendations page "
            "(or run `sentinel scan` from a terminal) to pull a first batch."
        )
    else:
        df = pd.DataFrame([dict(r) for r in rows])
        df = df.rename(columns={
            "source": "Source",
            "c1h": "Last 1h",
            "c4h": "Last 4h",
            "c24h": "Last 24h",
            "total": "Total cached",
            "newest": "Most recent article",
        })
        df["Most recent article"] = df["Most recent article"].apply(
            lambda s: fmt_ts(s) if s else "—"
        )
        st.dataframe(df, hide_index=True, width="stretch")

        active_tiers = {r["source"] for r in rows if r["c4h"] > 0}
        if active_tiers:
            st.success(
                f"Live in last 4h: **{', '.join(sorted(active_tiers))}**. "
                "These contribute to sentiment qualification."
            )
        else:
            st.warning(
                "No articles newer than 4h — sentiment won't qualify any symbol "
                "until fresher articles arrive. Run another scan during market hours."
            )

    # ---------- last scan ----------
    st.subheader("Last scan")
    recent = decisions.list_recent(cfg.storage.db_path, kind="regime", limit=1)
    if recent:
        st.write(
            f"Most recent regime evaluation (= end of a scan): **{fmt_ts(recent[0]['ts'])}**"
        )
    else:
        st.caption("No scans recorded yet.")
