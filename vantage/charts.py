"""Price series for the dashboard charts. Ranges mirror what a broker app offers; cached briefly in memory."""
from __future__ import annotations
import json, re, time
from datetime import datetime
import yfinance as yf
from .config import DATA
from .data import _yf_sym

# range -> (yfinance period, bar interval, cache seconds)
RANGES = {"1d": ("1d", "5m", 60), "1w": ("5d", "30m", 300), "1m": ("1mo", "1h", 900),
          "1y": ("1y", "1d", 1800), "5y": ("5y", "1wk", 3600)}
_cache: dict = {}
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")


def first_pick(ticker: str) -> dict | None:
    """Earliest logged recommendation of this ticker (any sleeve) — drawn as the 'picked' marker."""
    p = DATA / "picks.jsonl"
    if not p.exists():
        return None
    best = None
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["ticker"] == ticker and (best is None or r["date"] < best["date"]):
            best = {"date": r["date"], "price": r["price"], "sleeve": r.get("sleeve", "core")}
    return best


def series(ticker: str, rng: str) -> dict:
    if rng not in RANGES or not _TICKER.match(ticker or ""):
        return {"error": "bad ticker or range"}
    period, interval, ttl = RANGES[rng]
    hit = _cache.get((ticker, rng))
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    tk = yf.Ticker(_yf_sym(ticker))
    h = tk.history(period=period, interval=interval, auto_adjust=True)
    c = h["Close"].dropna() if len(h) else []
    pts = [[int(ts.timestamp() * 1000), round(float(v), 4)] for ts, v in c.items()]
    if len(pts) < 2:
        return {"error": f"no price data for {ticker} ({rng})"}
    out = {"ticker": ticker, "range": rng, "interval": interval, "points": pts, "first": pts[0][1], "last": pts[-1][1],
           "change_pct": round((pts[-1][1] / pts[0][1] - 1) * 100, 2), "pick": first_pick(ticker),
           "asof": datetime.now().isoformat(timespec="seconds"), "prev_close": None}
    if rng == "1d":   # the day view is read against yesterday's close, not the first 5-minute bar
        try:
            d = tk.history(period="5d", interval="1d", auto_adjust=True)["Close"].dropna()
            session = c.index[-1].date()
            prior = d[[i.date() < session for i in d.index]]
            if len(prior):
                out["prev_close"] = round(float(prior.iloc[-1]), 4)
                out["change_pct"] = round((pts[-1][1] / out["prev_close"] - 1) * 100, 2)
        except Exception:
            pass
    _cache[(ticker, rng)] = (time.time(), out)
    return out
