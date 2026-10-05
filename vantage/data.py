"""Market data via yfinance: batch prices, intraday, fundamentals, insiders, analysts, macro."""
from __future__ import annotations
import warnings, math, json, time
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf
from .config import CACHE

warnings.filterwarnings("ignore")

# yfinance keeps a sqlite timezone/cookie cache under ~/.cache; under launchd that intermittently fails with
# "unable to open database file" and silently drops tickers. Use a project-local cache instead.
try:
    yf.set_tz_cache_location(str(CACHE / "yf"))
except Exception:
    pass


def _yf_sym(t: str) -> str:
    return t.replace(".", "-")


def _dl_close(syms: list[str], period: str) -> pd.DataFrame:
    d = yf.download(syms, period=period, progress=False, auto_adjust=True, threads=True)
    if d is None or d.empty:
        return pd.DataFrame()
    c = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": syms[0]})
    c.columns = [str(x).replace("-", ".") for x in c.columns]
    return c.dropna(axis=1, how="all")


def prices(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Wide adjusted-close DataFrame (columns = tickers). Batched; failed tickers are retried once."""
    syms = [_yf_sym(t) for t in tickers]
    out = [_dl_close(syms[i:i + 200], period) for i in range(0, len(syms), 200)]
    px = pd.concat([o for o in out if not o.empty], axis=1)
    missing = [t for t in tickers if t not in px.columns]
    if missing:
        time.sleep(2)
        retry = _dl_close([_yf_sym(t) for t in missing], period)
        if not retry.empty:
            px = pd.concat([px, retry], axis=1)
        still = [t for t in tickers if t not in px.columns]
        print(f"prices: {len(missing)} tickers failed first pass, {len(still)} still unavailable after retry: {still[:12]}", flush=True)
    return px.loc[:, ~px.columns.duplicated()]


def volumes(tickers: list[str], period: str = "6mo") -> pd.DataFrame:
    syms = [_yf_sym(t) for t in tickers]
    d = yf.download(syms, period=period, progress=False, auto_adjust=True, threads=True)
    v = d["Volume"] if isinstance(d.columns, pd.MultiIndex) else d[["Volume"]]
    v.columns = [c.replace("-", ".") for c in v.columns]
    return v


def intraday(ticker: str) -> dict:
    """Today's 1-minute bars summarized: last, open, day % move, minutes since last bar."""
    try:
        h = yf.Ticker(_yf_sym(ticker)).history(period="1d", interval="1m")
        if h.empty:
            return {}
        last = float(h["Close"].iloc[-1]); opn = float(h["Open"].iloc[0])
        ts = h.index[-1].to_pydatetime()
        age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 60
        return {"last": round(last, 2), "day_pct": round((last / opn - 1) * 100, 2),
                "asof": ts.isoformat(), "minutes_old": round(age, 1), "bars": len(h)}
    except Exception:
        return {}


_KEYS = ["shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "priceToBook",
         "enterpriseToEbitda", "returnOnEquity", "profitMargins", "operatingMargins", "grossMargins",
         "revenueGrowth", "earningsGrowth", "debtToEquity", "currentRatio", "freeCashflow",
         "operatingCashflow", "totalCash", "totalDebt", "beta", "dividendYield", "payoutRatio",
         "targetMeanPrice", "recommendationMean", "recommendationKey", "numberOfAnalystOpinions",
         "shortPercentOfFloat", "heldPercentInstitutions", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
         "currentPrice", "longBusinessSummary"]


def fundamentals(ticker: str) -> dict:
    """Selected .info fields + insider net buying (6mo) + analyst rec trend + next earnings."""
    tk = yf.Ticker(_yf_sym(ticker))
    out = {"ticker": ticker}
    try:
        info = tk.info or {}
        for k in _KEYS:
            v = info.get(k)
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = None
            out[k] = v
        if out.get("longBusinessSummary"):
            out["longBusinessSummary"] = out["longBusinessSummary"][:600]
    except Exception:
        pass
    # insiders: open-market buys vs sells, last 6 months
    try:
        it = tk.insider_transactions
        if it is not None and len(it):
            it = it.copy()
            it["Start Date"] = pd.to_datetime(it["Start Date"], errors="coerce")
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=180)
            rec = it[it["Start Date"] >= cutoff]
            txt = rec["Text"].astype(str).str.lower() if "Text" in rec else pd.Series([], dtype=str)
            buys = rec[txt.str.contains("purchase", na=False)]
            sells = rec[txt.str.contains("sale", na=False) & ~txt.str.contains("option", na=False)]
            out["insider_buy_value"] = float(buys["Value"].fillna(0).sum()) if "Value" in buys else 0.0
            out["insider_sell_value"] = float(sells["Value"].fillna(0).sum()) if "Value" in sells else 0.0
            out["insider_buys_n"] = int(len(buys)); out["insider_sells_n"] = int(len(sells))
            out["insider_recent"] = [
                {"date": str(r["Start Date"].date()) if pd.notna(r["Start Date"]) else None,
                 "who": str(r.get("Insider", ""))[:40], "text": str(r.get("Text", ""))[:80],
                 "value": None if pd.isna(r.get("Value")) else float(r.get("Value"))}
                for _, r in rec.head(6).iterrows()]
    except Exception:
        pass
    # analyst recommendation drift (latest month vs 3 months ago)
    try:
        r = tk.recommendations
        if r is not None and len(r) >= 2:
            def score(row):
                n = row[["strongBuy", "buy", "hold", "sell", "strongSell"]].sum()
                return None if n == 0 else float((row["strongBuy"] * 2 + row["buy"] - row["sell"] - row["strongSell"] * 2) / n)
            now, past = score(r.iloc[0]), score(r.iloc[-1])
            out["rec_score_now"] = now; out["rec_score_past"] = past
            out["rec_drift"] = None if (now is None or past is None) else round(now - past, 3)
    except Exception:
        pass
    try:
        c = tk.calendar
        ed = c.get("Earnings Date") if isinstance(c, dict) else None
        if ed:
            d0 = ed[0] if isinstance(ed, list) else ed
            days = (pd.Timestamp(d0) - pd.Timestamp.now().normalize()).days
            if days >= 0:   # yfinance sometimes returns the LAST earnings date; a past date is not a catalyst
                out["next_earnings"] = str(d0)
                out["days_to_earnings"] = days
    except Exception:
        pass
    # last reported quarter: days since + EPS surprise % (post-earnings-drift input for the weekly sleeve)
    try:
        ed = tk.earnings_dates
        if ed is not None and len(ed):
            ed = ed.copy(); ed.index = pd.to_datetime(ed.index, errors="coerce"); ed = ed[ed.index.notna()]
            now = pd.Timestamp.now(tz=ed.index.tz) if ed.index.tz is not None else pd.Timestamp.now()
            past = ed[ed.index <= now].sort_index()
            if len(past):
                last = past.index[-1]
                out["last_earnings"] = str(last.date()); out["days_since_earnings"] = int((now.normalize() - last.normalize()).days)
                sp = past.iloc[-1].get("Surprise(%)") if "Surprise(%)" in past.columns else None
                out["eps_surprise_pct"] = None if sp is None or pd.isna(sp) else round(float(sp), 1)
    except Exception:
        pass
    return out


def macro() -> dict:
    """Regime snapshot: VIX, 10y yield, dollar, oil, gold, index returns over 1d/1w/1m/3m."""
    syms = {"SPY": "S&P 500", "QQQ": "Nasdaq", "IWM": "Small caps", "^VIX": "VIX", "^TNX": "10y yield",
            "DX-Y.NYB": "Dollar", "CL=F": "Crude", "GC=F": "Gold", "TLT": "Long bonds", "HYG": "HY credit"}
    d = yf.download(list(syms), period="6mo", progress=False, auto_adjust=True, threads=True)["Close"]
    out = {}
    for s, name in syms.items():
        if s not in d:
            continue
        c = d[s].dropna()
        if len(c) < 65:
            continue
        lvl = float(c.iloc[-1])
        rets = {k: round((lvl / float(c.iloc[-1 - n]) - 1) * 100, 2) for k, n in (("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63))}
        out[name] = {"level": round(lvl, 2), **rets}
    # regime tags
    vix = out.get("VIX", {}).get("level", 20)
    spy3m = out.get("S&P 500", {}).get("3m", 0)
    out["_regime"] = {
        "volatility": "calm" if vix < 15 else ("normal" if vix < 22 else ("stressed" if vix < 30 else "crisis")),
        "trend": "risk-on" if spy3m > 3 else ("risk-off" if spy3m < -3 else "range-bound"),
        "asof": datetime.now().isoformat(timespec="minutes"),
    }
    return out
