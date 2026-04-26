"""Unit tests for the AI Analyst response parser — especially partial recovery."""
from __future__ import annotations

from sentinel.intelligence.claude_analyst import _parse_response


def test_parse_clean_json() -> None:
    text = """{"market_assessment":"OK","recommendations":[{"symbol":"AAPL","thesis":"x","catalysts_cited":[],"conviction":"low","risk_factors":[],"time_horizon":"swing","inputs_used":[]}]}"""
    out = _parse_response(text)
    assert out["recommendations"][0]["symbol"] == "AAPL"


def test_parse_with_code_fences() -> None:
    text = '```json\n{"market_assessment":"x","recommendations":[]}\n```'
    out = _parse_response(text)
    assert out["recommendations"] == []


def test_parse_recovers_truncated_array() -> None:
    """Simulate a max_tokens cutoff mid-string in the 3rd recommendation —
    recover the first 2."""
    text = """{
  "market_assessment": "OK",
  "recommendations": [
    {"symbol":"NVDA","thesis":"good","catalysts_cited":[],"conviction":"high","risk_factors":[],"time_horizon":"swing","inputs_used":[]},
    {"symbol":"AMD","thesis":"also good","catalysts_cited":[],"conviction":"medium","risk_factors":[],"time_horizon":"intraday","inputs_used":[]},
    {"symbol":"TSLA","thesis":"this one got cu"""
    out = _parse_response(text)
    assert len(out["recommendations"]) == 2
    assert out["recommendations"][0]["symbol"] == "NVDA"
    assert out["recommendations"][1]["symbol"] == "AMD"
