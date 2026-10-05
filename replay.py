#!/usr/bin/env python3
"""Historical replay of the PRICE-FACTOR screen (momentum / trend / low-vol) — a diagnostic, not a backtest
of the full desk. Fundamentals, insiders, news, 13Fs and the reasoning layer have no point-in-time source
in free data, so they are NOT replayed. This answers one narrow question honestly: has the stage-1 screen,
on its own, beaten SPY over the last ~15 months? Results go to data/replay.json, separate from live picks."""
from __future__ import annotations
import json, math, warnings, sys, time
from datetime import date
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from vantage import universe, data, factors
from vantage.config import DATA, BENCHMARK, WEIGHTS
from vantage.track import cluster_t, tail_share, judge

STEP, TOP, HORIZONS = 10, 12, {"1w": 5, "1m": 21, "3m": 63}


def main(months: int = 15):
    t0 = time.time()
    U = universe.full(); kinds = {t: v["kind"] for t, v in U.items()}
    px = data.prices(list(U), period="3y").ffill()
    px = px[[c for c in px.columns if c in U]]
    idx = px.index
    start = idx.searchsorted(idx[-1] - pd.Timedelta(days=30 * months))
    dates = list(range(max(start, 260), len(idx) - 1, STEP))
    print(f"replay: {len(dates)} rebalance dates from {idx[dates[0]].date()} to {idx[dates[-1]].date()} ({px.shape[1]} names)")
    picks, port_curve = [], []
    for i in dates:
        d = idx[i]
        s1 = factors.stage1(px.iloc[:i + 1], kinds)
        top = s1[s1["kind"] == "stock"].head(TOP)
        for rk, (t, row) in enumerate(top.iterrows(), 1):
            rec = {"date": str(d.date()), "ticker": t, "rank": rk, "stage1": round(float(row["stage1"]), 3),
                   "z_momentum": round(float(row["z_momentum"]), 2), "z_trend": round(float(row["z_trend"]), 2), "z_lowvol": round(float(row["z_lowvol"]), 2)}
            p0, s0 = float(px[t].iloc[i]), float(px[BENCHMARK].iloc[i])
            for h, n in HORIZONS.items():
                if i + n < len(idx):
                    rec[h] = round((float(px[t].iloc[i + n]) / p0 - float(px[BENCHMARK].iloc[i + n]) / s0) * 100, 2)
            picks.append(rec)
    summary = {}
    for h in HORIZONS:
        ex = np.array([p[h] for p in picks if h in p]); top5 = np.array([p[h] for p in picks if h in p and p["rank"] <= 5])
        if len(ex):
            summary[h] = {"n": len(ex), "mean_excess_pct": round(float(ex.mean()), 2), "median_excess_pct": round(float(np.median(ex)), 2),
                          "hit_rate_pct": round(float((ex > 0).mean() * 100)), "top5_mean_excess_pct": round(float(top5.mean()), 2) if len(top5) else None,
                          "t_stat_naive": round(float(ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))), 2) if len(ex) > 2 else None,
                          **cluster_t([(p["date"], p[h]) for p in picks if h in p]), **tail_share(ex)}
    episodes = len({p["ticker"] for p in picks})
    concentration = {}
    for h in ("1m", "3m"):
        by = {}
        for p in picks:
            if h in p:
                by[p["ticker"]] = by.get(p["ticker"], 0) + p[h]
        tot = sum(v for v in by.values() if v > 0) or 1
        top = sorted(by.items(), key=lambda kv: -kv[1])[:5]
        concentration[h] = {"top5_names": [(t, round(v, 1)) for t, v in top], "top5_share_of_positive_excess": round(sum(v for _, v in top) / tot, 2)}
    # top-5 equal-weight portfolio, held STEP days each rebalance, vs SPY (gross, no costs)
    port, bench = 1.0, 1.0
    for j, i in enumerate(dates[:-1]):
        nxt = dates[j + 1]
        names = [p["ticker"] for p in picks if p["date"] == str(idx[i].date()) and p["rank"] <= 5]
        rets = [float(px[t].iloc[nxt]) / float(px[t].iloc[i]) - 1 for t in names]
        port *= 1 + float(np.mean(rets)); bench *= float(px[BENCHMARK].iloc[nxt]) / float(px[BENCHMARK].iloc[i])
    verdict = "PRICE SCREEN ONLY: " + judge(summary.get("1m", {}))
    caveats = ["Price factors only (momentum/trend/low-vol). Fundamentals, insiders, news, 13Fs and the reasoning layer are NOT replayed — no point-in-time source in free data.",
               "SURVIVORSHIP BIAS: uses TODAY's S&P 500 list for past dates. Names added because they soared are included; names dropped because they fell are excluded. This inflates momentum results.",
               f"Only {episodes} distinct names across {len(picks)} picks — the same winners recur across rebalances, so per-pick t-stats are overstated; use t_cluster (by date).",
               "Gross of transaction costs; 10-day rebalance of a concentrated top-5 is high-turnover.",
               "One 15-month window = one regime. Momentum's known failure mode is the crash at the end of a run like this."]
    out = {"kind": "PRICE-FACTORS-ONLY replay (momentum/trend/low-vol screen). Not the full desk. Diagnostic, not a backtest to size money on.",
           "generated": date.today().isoformat(), "caveats": caveats,
           "window": {"from": str(idx[dates[0]].date()), "to": str(idx[dates[-1]].date()), "rebalances": len(dates), "step_days": STEP, "picks_per_date": TOP},
           "by_horizon": summary, "concentration": concentration, "distinct_names": episodes,
           "top5_portfolio": {"cum_ret_pct": round((port - 1) * 100, 2), "spy_cum_ret_pct": round((bench - 1) * 100, 2), "excess_pct": round((port - bench) * 100, 2)},
           "verdict": verdict, "weights_used": {k: WEIGHTS[k] for k in ("momentum", "trend", "lowvol")}, "n_picks": len(picks), "runtime_s": round(time.time() - t0)}
    (DATA / "replay.json").write_text(json.dumps(out, indent=1))
    (DATA / "replay_picks.jsonl").write_text("\n".join(json.dumps(p) for p in picks))
    print(json.dumps({k: v for k, v in out.items() if k != "weights_used"}, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
