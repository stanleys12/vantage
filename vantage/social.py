"""Social layer: StockTwits (free, no key). Reddit blocks unauthenticated access; X API is paid.
Sentiment here is CONTEXT ONLY (capped weight) — the desk is informed by the crowd, never led by it."""
from __future__ import annotations
import time, requests
from .config import UA

S = requests.Session(); S.headers.update(UA)


def stocktwits(ticker: str) -> dict:
    try:
        r = S.get(f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json", timeout=15)
        if r.status_code != 200:
            return {"n": 0}
        msgs = r.json().get("messages", [])
    except Exception:
        return {"n": 0}
    bull = bear = 0
    for m in msgs:
        s = (m.get("entities") or {}).get("sentiment") or {}
        if s.get("basic") == "Bullish": bull += 1
        elif s.get("basic") == "Bearish": bear += 1
    tagged = bull + bear
    return {"n": len(msgs), "bull": bull, "bear": bear,
            "bull_ratio": round(bull / tagged, 2) if tagged else None,
            "sample": [m.get("body", "")[:100] for m in msgs[:3]]}


def trending() -> list[str]:
    try:
        r = S.get("https://api.stocktwits.com/api/2/trending/symbols.json", timeout=15)
        return [s["symbol"] for s in r.json().get("symbols", []) if "." not in s["symbol"]][:20]
    except Exception:
        return []
