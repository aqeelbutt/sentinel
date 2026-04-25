"""Backtest harness — runs the same strategy code against historical bars.

Live and backtest share signals (`strategy/signals/*`), risk math (`risk/sizing.py`),
exit thresholds (`config.exit.*`), and indicator code (`strategy/indicators.py`).
What differs: the broker is simulated (next-bar-open fill + slippage) and the
"current time" is driven by historical bar timestamps instead of wall clock.
"""
