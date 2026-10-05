"""Scorecard: every pick is logged and later marked against SPY. This is the honesty engine."""
from __future__ import annotations
import json, math
from datetime import date
import numpy as np
import pandas as pd
from .config import DATA, BENCHMARK
from . import data as D

LOG = DATA / "picks.jsonl"
CARD = DATA / "scorecard.json"
HORIZONS = {"1w": 5, "1m": 21, "3m": 63}
MIN_DATES = 20   # distinct pick-dates before any verdict; same-day picks are one correlated observation


def cluster_t(date_excess: list[tuple[str, float]]) -> dict:
    """t-stat that treats each pick-date as ONE observation (picks made the same day are correlated;
    the same names recur across days). This is the honest t; the naive per-pick t overstates."""
    by = {}
    for d, e in date_excess:
        by.setdefault(d, []).append(float(e))
    means = np.array([np.mean(v) for v in by.values()])
    k = len(means)
    t = float(means.mean() / (means.std(ddof=1) / math.sqrt(k))) if k > 2 and means.std(ddof=1) > 0 else None
    return {"clusters": k, "t_cluster": round(t, 2) if t is not None else None}


def tail_share(excess: list[float]) -> dict:
    """How much of the total excess comes from the top 5% of picks, and the mean without them.
    If mean_ex_top5pct <= 0, the 'edge' is a handful of outliers, not a reliable tilt."""
    ex = np.sort(np.asarray(excess, float))[::-1]
    if len(ex) < 10:
        return {}
    k = max(1, int(round(0.05 * len(ex)))); tot = ex.sum()
    return {"top5pct_share_of_total": round(float(ex[:k].sum() / tot), 2) if tot > 0 else None,
            "mean_excess_ex_top5pct": round(float(ex[k:].mean()), 2)}


def judge(s: dict) -> str:
    """Shared verdict logic for a horizon summary dict (needs clusters, t_cluster, median, hit, tail)."""
    if not s or (s.get("clusters") or 0) < MIN_DATES:
        return (f"Only {s.get('clusters', 0)}/{MIN_DATES} independent pick-dates scored — any edge or lack of it is still noise. Keep logging."
                if s else "Too early — picks need at least a week of forward data before the scorecard means anything.")
    t = s.get("t_cluster") or 0
    med, hit, tail = s.get("median_excess_pct", 0), s.get("hit_rate_pct", 0), s.get("mean_excess_ex_top5pct")
    if t > 2 and med > 0 and hit >= 50 and (tail is None or tail > 0):
        return f"Edge that survives the checks: cluster t={t} over {s['clusters']} dates, median {med:+.2f}%, hit {hit}%, still positive without the top-5% winners. Promising — keep verifying."
    if t > 2:
        return (f"Positive MEAN (cluster t={t}) but median {med:+.2f}% / hit {hit}%"
                + (f" / {tail:+.2f}% without the top-5% winners" if tail is not None else "")
                + ": a few big winners carry it and most picks lose to SPY. That is the momentum profile, not a reliable edge, and it reverses hard in momentum crashes. Do not size up on it.")
    if t < -2:
        return f"Picks are UNDERPERFORMING SPY with significance (cluster t={t}). Do not follow; the model needs rework."
    return f"No statistically distinguishable edge (cluster t={t}, {s['clusters']} dates). Treat picks as research, not signal."


def record(picks: list[dict], spy_price: float, run_date: str | None = None, regime: str | None = None, sleeve: str = "core"):
    """Log picks with everything the learning loop needs later: factor z's, LLM adjust, verdict, regime, sleeve."""
    d = run_date or date.today().isoformat()
    existing = set()
    if LOG.exists():
        for l in LOG.read_text().splitlines():
            if l.strip():
                j = json.loads(l); existing.add((j["date"], j["ticker"], j.get("sleeve", "core")))
    with LOG.open("a") as fh:
        for p in picks:
            if (d, p["ticker"], sleeve) in existing:
                continue
            fh.write(json.dumps({"date": d, "sleeve": sleeve, "ticker": p["ticker"], "rank": p["rank"], "conviction": p["conviction"],
                                 "evidence_conviction": p.get("evidence_conviction", p["conviction"]),
                                 "adjust": p.get("adjust", 0), "verdict": p.get("verdict"),
                                 "price": p["price"], "spy": spy_price, "driver": p["driver"],
                                 "p_beat_spy": p["p_beat_spy"], "regime": regime, "z": p.get("z", {}),
                                 "thesis": (p.get("thesis") or "")[:240], "flip": (p.get("flip") or "")[:160]}) + "\n")


def joined() -> list[dict]:
    """Every logged pick joined with its scored outcomes (from the last evaluate())."""
    if not LOG.exists():
        return []
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    det = {}
    if CARD.exists():
        try:
            for r in json.loads(CARD.read_text()).get("detail", []):
                det[(r["date"], r["ticker"])] = r
        except Exception:
            pass
    out = []
    for r in rows:
        o = det.get((r["date"], r["ticker"]), {})
        out.append({**r, **{k: o[k] for k in ("1w", "1m", "3m", "live") if k in o}})
    return out


def evaluate() -> dict:
    """Forward returns of past picks vs SPY at 1w/1m/3m, plus top-5 daily-rebalanced portfolio vs SPY."""
    if not LOG.exists():
        return {"n_picks": 0, "note": "no picks logged yet"}
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    if not rows:
        return {"n_picks": 0}
    tickers = sorted({r["ticker"] for r in rows} | {BENCHMARK})
    px = D.prices(tickers, period="1y").ffill()
    idx = px.index.tz_localize(None).normalize() if px.index.tz is not None else px.index.normalize()
    px.index = idx
    res = {h: [] for h in HORIZONS}
    dated = {h: [] for h in HORIZONS}
    detail = []
    for r in rows:
        d0 = pd.Timestamp(r["date"])
        pos = idx.searchsorted(d0)
        if pos >= len(idx) or r["ticker"] not in px:
            continue
        p0, s0 = float(px[r["ticker"]].iloc[pos]), float(px[BENCHMARK].iloc[pos])
        rec = {"date": r["date"], "ticker": r["ticker"], "rank": r["rank"], "conviction": r["conviction"], "sleeve": r.get("sleeve", "core")}
        for h, n in HORIZONS.items():
            if pos + n < len(idx):
                pr = float(px[r["ticker"]].iloc[pos + n]) / p0 - 1
                sr = float(px[BENCHMARK].iloc[pos + n]) / s0 - 1
                res[h].append((pr, sr, r["rank"], r.get("sleeve", "core")))
                dated[h].append((r["date"], (pr - sr) * 100, r.get("sleeve", "core")))
                rec[h] = {"ret": round(pr * 100, 2), "spy": round(sr * 100, 2), "excess": round((pr - sr) * 100, 2)}
        # live (to date) return
        pl = float(px[r["ticker"]].iloc[-1]) / p0 - 1; sl = float(px[BENCHMARK].iloc[-1]) / s0 - 1
        rec["live"] = {"ret": round(pl * 100, 2), "spy": round(sl * 100, 2), "excess": round((pl - sl) * 100, 2),
                       "days": int(len(idx) - 1 - pos)}
        detail.append(rec)
    def _summ(sleeve):
        out = {}
        for h, lst in res.items():
            sub = [x for x in lst if x[3] == sleeve]
            if not sub:
                continue
            ex = np.array([p - s_ for p, s_, _, _ in sub]); top5 = np.array([p - s_ for p, s_, rk, _ in sub if rk <= 5])
            out[h] = {"n": len(sub), "hit_rate_pct": round(float((ex > 0).mean() * 100)),
                      "mean_excess_pct": round(float(ex.mean() * 100), 2),
                      "median_excess_pct": round(float(np.median(ex) * 100), 2),
                      "top5_mean_excess_pct": round(float(top5.mean() * 100), 2) if len(top5) else None,
                      "t_stat": round(float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))), 2) if len(ex) > 2 and ex.std() > 0 else None,
                      **cluster_t([(d, e) for d, e, sl in dated[h] if sl == sleeve]), **tail_share(ex * 100)}
        return out
    summary = _summ("core")
    sleeves = {}
    for sl in sorted({r.get("sleeve", "core") for r in rows} - {"core"}):
        bh = _summ(sl)
        sleeves[sl] = {"n_picks": sum(1 for r in rows if r.get("sleeve") == sl), "by_horizon": bh,
                       "verdict": judge(bh.get("1w") or {})}   # weekly sleeve is judged at its native 1-week horizon
    # daily-rebalanced top-5 equal-weight portfolio (each day's top-5 held one day)
    by_day = {}
    for r in rows:
        if r["rank"] <= 5 and r.get("sleeve", "core") == "core":
            by_day.setdefault(r["date"], []).append(r["ticker"])
    port, bench = 1.0, 1.0
    days = sorted(by_day)
    for i, d in enumerate(days):
        d0 = pd.Timestamp(d); pos = idx.searchsorted(d0)
        nxt = idx.searchsorted(pd.Timestamp(days[i + 1])) if i + 1 < len(days) else len(idx) - 1
        # a pick-date can sit beyond the last price bar (pre-market 6:20 run) → searchsorted == len(idx)
        if pos >= len(idx) or nxt >= len(idx) or nxt <= pos:
            continue
        rets = [float(px[t].iloc[nxt]) / float(px[t].iloc[pos]) - 1 for t in by_day[d] if t in px]
        if rets:
            port *= 1 + float(np.mean(rets)); bench *= float(px[BENCHMARK].iloc[nxt]) / float(px[BENCHMARK].iloc[pos])
    core = [r for r in rows if r.get("sleeve", "core") == "core"]
    card = {"n_picks": len(core), "n_days": len(days), "first": rows[0]["date"], "last": rows[-1]["date"],
            "by_horizon": summary, "sleeves": sleeves,
            "top5_portfolio": {"cum_ret_pct": round((port - 1) * 100, 2), "spy_cum_ret_pct": round((bench - 1) * 100, 2),
                               "excess_pct": round((port - bench) * 100, 2)},
            "verdict": _verdict(summary), "detail": detail[-200:]}
    CARD.write_text(json.dumps(card, indent=1))
    return card


def _verdict(summary: dict) -> str:
    return judge(summary.get("1m") or summary.get("1w") or {})
