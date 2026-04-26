"""AI Analyst — Claude-powered structured analysis of the current market state.

The pipeline:
  1. snapshot.build_market_snapshot()  → structured dict of everything Claude sees
  2. claude_analyst.run_analyst()      → API call, parse JSON, persist
  3. repos/ai_recommendations          → query for the dashboard / CLI

Same RiskManager gates apply when the user clicks 'Add to portfolio' on an AI rec.
"""
