#!/usr/bin/env python3
"""Vantage daily run: screen → deep-dive → reason → rank → record → score → brief.
Usage: run_daily.py [--fast] [--no-record] [--deep N] [--final N] [--model MODEL]"""
from __future__ import annotations
import argparse, json, sys, time, warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from vantage import universe, data, news, edgar, social, factors, rank, reason, track, report, learn, weekly, paper
from vantage.config import DEEP_DIVE_N, LOWVOL_SLEEVE, LLM_N, FINAL_N, BENCHMARK, REPORTS

LOGF = REPORTS / "run.log"


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOGF.open("a") as fh:
        fh.write(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="skip LLM reasoning and gurus (smoke test)")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--deep", type=int, default=DEEP_DIVE_N)
    ap.add_argument("--final", type=int, default=FINAL_N)
    ap.add_argument("--model", default=None)
    ap.add_argument("--sleeves", default="core,weekly", help="which sleeves to record (core,weekly)")
    a = ap.parse_args()
    t0 = time.time(); today = date.today().isoformat()
    log(f"=== Vantage run {today} {'(fast)' if a.fast else ''} ===")

    U = universe.full(); kinds = {t: v["kind"] for t, v in U.items()}
    log(f"universe {len(U)}")
    px = data.prices(list(U), period="2y")
    px = px[[c for c in px.columns if c in U]]
    log(f"prices {px.shape[1]} names × {px.shape[0]} days (latest {px.index[-1].date()})")

    s1 = factors.stage1(px, kinds)
    st = s1[s1["kind"] == "stock"]
    core = st.head(a.deep)
    sleeve = st.sort_values("z_lowvol", ascending=False).head(LOWVOL_SLEEVE)
    stocks = pd.concat([core, sleeve[~sleeve.index.isin(core.index)]])
    etfs = s1[s1["kind"] == "etf"].head(5)
    cands = pd.concat([stocks, etfs])
    log(f"stage1 → {len(core)} core + {len(stocks)-len(core)} low-vol sleeve + {len(etfs)} ETFs: {list(core.index[:8])}…")

    # deep-dive in parallel: fundamentals, news, social
    def deep(t):
        n = U[t]["name"]
        f = data.fundamentals(t) if kinds[t] == "stock" else {"ticker": t, "shortName": n}
        return t, f, news.ticker_news(t, n if kinds[t] == "stock" else None), social.stocktwits(t)
    fund, newsd, socd = {}, {}, {}
    with ThreadPoolExecutor(8) as ex:
        for t, f, nw, so in ex.map(deep, list(cands.index)):
            fund[t], newsd[t], socd[t] = f, nw, so
    log(f"deep-dive done ({time.time()-t0:.0f}s)")

    gurus = {} if a.fast else edgar.guru_snapshot()
    names = {t: v["name"] for t, v in U.items() if v["kind"] == "stock"}
    ghits = edgar.match_guru_holdings(gurus, names) if gurus else {}
    log(f"gurus {sum(1 for g in gurus.values() if 'top' in g)}/{len(gurus)} ok, {len(ghits)} universe names held")

    s2 = factors.stage2(cands, fund, newsd, socd)
    macro = data.macro(); mkt = news.market_news()
    rf = (macro.get("10y yield", {}).get("level", 4.0)) / 100
    spy_px = float(px[BENCHMARK].iloc[-1])

    cl = []
    for t, row in s2.iterrows():
        f = fund[t]
        ri = rank.build(row, rf, f.get("beta"))
        cl.append({"ticker": t, "name": U[t]["name"], "sector": U[t]["sector"], "kind": kinds[t],
                   "price": round(float(row["price"]), 2), "conviction": int(row["conviction"]),
                   "z": {"momentum": float(row["z_momentum"]), "trend": float(row["z_trend"]), "quality": float(row["z_quality"]),
                         "value": float(row["z_value"]), "lowvol": float(row["z_lowvol"]), "insider": float(row["z_insider"]),
                         "revision": float(row["z_revision"]), "catalyst": float(row["catalyst"]), "context": float(row["context"])},
                   "ret_1w": round(float(row["ret_1w"]) * 100, 1), "ret_1m": round(float(row["ret_1m"]) * 100, 1), "ret_3m": round(float(row["ret_3m"]) * 100, 1),
                   "ret_6m": round(float(row["ret_6m"]) * 100, 1), "pct_52w": round(float(row["pct_of_52w_high"]) * 100, 1),
                   "rank_info": ri, "fundamentals": {k: v for k, v in f.items() if k != "insider_recent"},
                   "insider_recent": f.get("insider_recent", []), "news": newsd[t], "social": socd[t],
                   "gurus": ghits.get(t, []), "risk_flags": rank.risk_flags(row, f)})

    # LLM reasons over the evidence-ranked shortlist; the tail gets rule-based text.
    cl.sort(key=lambda c: -c["conviction"])
    rs = reason.reason(cl[:LLM_N], macro, mkt, gurus, model=a.model, use_llm=not a.fast)
    tail = reason.reason(cl[LLM_N:], macro, mkt, gurus, use_llm=False) if len(cl) > LLM_N else {"picks": {}}
    rs["picks"].update({k: v for k, v in tail["picks"].items() if k not in rs["picks"]})
    if a.fast:   # afternoon refresh: keep the morning's written reasoning instead of overwriting it with rule-based text
        try:
            prior = json.loads((REPORTS / "latest.json").read_text())
            pr = prior.get("reasoning") or {}
            if prior.get("date") == today and str(pr.get("source", "")).startswith("claude"):
                rs["picks"].update({t: v for t, v in pr.get("picks", {}).items() if not v.get("_fallback")})
                for k in ("market_view", "top_pick_rationale", "what_i_ignored", "cost_usd", "model"):
                    if pr.get(k) is not None: rs[k] = pr[k]
                rs["source"] = "claude (morning run, reused)"
        except Exception:
            pass
    for c in cl:
        p = rs["picks"].get(c["ticker"]) or {"verdict": "Watch", "adjust": 0, "thesis": "", "lenses": {}, "key_risk": "", "change_my_mind": ""}
        c["reasoning"] = p
        c["final_conviction"] = int(max(0, min(100, c["conviction"] + int(p.get("adjust", 0)))))
    log(f"reasoning: {rs.get('source')} cost=${rs.get('cost_usd')} model={rs.get('model')} err={rs.get('error')}")

    cl.sort(key=lambda c: (-c["final_conviction"], -c["rank_info"]["p_beat_spy"]))
    picks = [c for c in cl if c["reasoning"].get("verdict") != "Avoid"][:a.final]
    for i, p in enumerate(picks, 1):
        p["rank"] = i
        p["intraday"] = data.intraday(p["ticker"])
    watch = [c for c in cl if c not in picks][:12]
    for c in watch:
        c["rank"] = None

    weekly_picks, weekly_excluded = weekly.select(cl, rf)
    log(f"weekly sleeve: {[(w['ticker'], w['bucket'], w['weekly_score']) for w in weekly_picks]}")
    reg = macro.get("_regime", {})
    sleeves = {x.strip() for x in a.sleeves.split(",") if x.strip()}
    if not a.no_record and "weekly" in sleeves:
        track.record([{"ticker": w["ticker"], "rank": w["rank"], "conviction": w["final_conviction"], "evidence_conviction": w["conviction"],
                       "adjust": (w.get("reasoning") or {}).get("adjust", 0), "verdict": w["bucket"], "price": w["price"], "driver": "weekly",
                       "p_beat_spy": w["rank_info"]["p_beat_spy"], "z": {k: round(v, 3) for k, v in w["z"].items()},
                       "thesis": "; ".join(w["reasons"]), "flip": "5 trading days elapsed"} for w in weekly_picks],
                     spy_px, today, regime=f"{reg.get('volatility')}/{reg.get('trend')}", sleeve="weekly")
    if not a.no_record and "core" in sleeves:
        track.record([{"ticker": p["ticker"], "rank": p["rank"], "conviction": p["final_conviction"],
                       "evidence_conviction": p["conviction"], "adjust": p["reasoning"].get("adjust", 0),
                       "verdict": p["reasoning"].get("verdict"), "price": p["price"], "driver": p["rank_info"]["driver"],
                       "p_beat_spy": p["rank_info"]["p_beat_spy"], "z": {k: round(v, 3) for k, v in p["z"].items()},
                       "thesis": p["reasoning"].get("thesis"), "flip": p["reasoning"].get("change_my_mind")} for p in picks],
                     spy_px, today, regime=f"{reg.get('volatility')}/{reg.get('trend')}")
    scorecard = track.evaluate()
    paper_book = paper.step([{"ticker": p["ticker"], "rank": p["rank"], "verdict": p["reasoning"].get("verdict"), "price": p["price"]} for p in picks],
                            [{"ticker": w["ticker"], "price": w["price"]} for w in weekly_picks], today,
                            trade=(not a.no_record and not a.fast and "core" in sleeves))
    log(f"paper: {'on' if paper_book.get('enabled') else 'off'} — " + (f"equity ${paper_book['equity']:,.0f} ({paper_book['total_return_pct']:+.2f}%), SPY sleeve {paper_book.get('spy_return_pct')}%" if paper_book.get('enabled') else str(paper_book.get('note'))[:110]))
    learning = learn.update()
    log(f"learning: {learning['n_scored']} scored, λ={learning['lambda']}, changes={learning['changes'] or learning.get('note')}")
    learn_pack = {"summary": learning}
    for name, p in (("attribution", learn.ATTR), ("learned", learn.LEARNED_PATH), ("replay", learn.DATA / "replay.json")):
        try:
            learn_pack[name] = json.loads(p.read_text()) if p.exists() else None
        except Exception:
            learn_pack[name] = None
    learn_pack["lessons_md"] = learn.LESSONS.read_text() if learn.LESSONS.exists() else ""

    run = {"date": today, "generated_at": datetime.now().isoformat(timespec="minutes"), "n_universe": len(U),
           "n_deep": len(cl), "macro": macro, "market_news": mkt, "gurus": gurus, "reasoning": rs,
           "picks": picks, "watchlist": [{k: c[k] for k in ("ticker", "name", "final_conviction", "conviction") } | {"verdict": c["reasoning"].get("verdict")} for c in watch],
           "scorecard": scorecard, "learning": learn_pack, "weekly": weekly_picks, "weekly_excluded": weekly_excluded, "paper": paper_book, "weights_used": factors.weights(),
           "runtime_s": round(time.time() - t0), "rf": rf, "spy": spy_px}
    md, js = report.write(run)
    log(f"brief → {md} ({run['runtime_s']}s)")
    print("\nWEEKLY SLEEVE (5-day holds, paper-first):")
    for w in weekly_picks:
        e = w["rank_info"]["expected_return_pct"]
        print(f"  {w['rank']:>2}. {w['ticker']:<6} score {w['weekly_score']:+.2f} [{w['bucket']}] 1w {e['bear']:+.1f}/{e['base']:+.2f}/{e['bull']:+.1f}%  P(beat) {w['rank_info']['p_beat_spy']}%  — {'; '.join(w['reasons'])[:90]}")
    print("\nTOP PICKS:")
    for p in picks:
        r = p["rank_info"]
        print(f"  {p['rank']:>2}. {p['ticker']:<6} conv {p['final_conviction']:>3} {p['reasoning'].get('verdict'):<6} {r['driver']:<9} {r['horizon']:<14} exc {r['expected_excess_annual_pct']:+.1f}%/yr  base {r['expected_return_pct']['base']:+.1f}%  P(beat) {r['p_beat_spy']}%")


if __name__ == "__main__":
    main()
