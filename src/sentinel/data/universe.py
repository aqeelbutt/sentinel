"""Discovery universe — liquid US equities Sentinel can recommend FROM
even if they're not in the user's watchlist.

Refreshed periodically by hand; covers:
  - Mega-cap tech, finance, healthcare (S&P 100 leaders)
  - High-volume trader favorites (memes, biotech, semis)
  - Major sector ETFs

Scan cost is linear in this size — 150 symbols × ~1s/symbol ≈ 2-3 minutes per
discovery scan. Don't add penny stocks; the liquidity filter would reject them
anyway, just wastes API calls.
"""

DISCOVERY_UNIVERSE: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL", "ADBE",
    "CRM", "AMD", "INTC", "MU", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "MRVL",
    # AI / semis / data center beneficiaries
    "SMCI", "ARM", "ASML", "TSM", "NXPI", "ON", "MCHP", "ANET", "MDB", "SNOW",
    "DDOG", "NET", "PANW", "CRWD", "ZS", "OKTA", "TEAM", "FTNT",
    # Cloud / SaaS
    "WDAY", "NOW", "INTU", "PLTR", "U",
    # Communication / media
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "WBD", "PARA",
    # E-commerce / payments
    "SHOP", "SQ", "PYPL", "MA", "V", "AXP", "COIN",
    # Finance — banks
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BX", "BLK", "KKR",
    # Healthcare
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "LLY", "TMO", "ABT", "DHR", "CVS",
    "ISRG", "AMGN", "GILD", "BMY", "REGN", "VRTX",
    # Consumer / retail
    "WMT", "TGT", "COST", "HD", "LOW", "MCD", "SBUX", "NKE", "LULU", "ULTA",
    # Industrials / defense
    "CAT", "DE", "HON", "GE", "LMT", "RTX", "NOC", "BA",
    # Energy
    "XOM", "CVX", "COP", "OXY", "SLB", "EOG",
    # Auto / EV
    "F", "GM", "RIVN", "LCID", "NIO",
    # Travel
    "BKNG", "ABNB", "UBER", "LYFT", "DAL", "UAL", "AAL",
    # Crypto-adjacent / miners
    "MARA", "RIOT", "MSTR", "HOOD",
    # High-vol biotech / specialty pharma
    "MRNA", "BNTX", "NVAX", "PTON",
    # Rate-sensitive popular names
    "PLUG", "FCEL", "CHWY", "DKNG", "ROKU", "PINS", "SNAP", "RBLX", "BMBL",
    # Major ETFs
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "ARKK", "SOXL", "TQQQ", "SQQQ",
    # Volatility
    "UVXY", "SVXY", "VXX",
]
