"""Daily brief: markdown + JSON, written to reports/."""
from __future__ import annotations
import json
from datetime import datetime
from .config import REPORTS, DISCLAIMER


def _pct(x):
    return "—" if x is None else f"{x:+.1f}%"


def write(run: dict) -> tuple[str, str]:
    d = run["date"]
    js = REPORTS / f"{d}.json"; md = REPORTS / f"{d}.md"
    js.write_text(json.dumps(run, indent=1, default=str))
    (REPORTS / "latest.json").write_text(js.read_text())

    L = [f"# Vantage daily brief — {d}", f"_Generated {run['generated_at']} · {run['n_universe']} names screened → {run['n_deep']} deep-dived → top {len(run['picks'])}_", ""]
    reg = run["macro"].get("_regime", {})
    L += [f"## Regime: **{reg.get('volatility')} volatility, {reg.get('trend')}**", ""]
    L += ["| | level | 1d | 1w | 1m | 3m |", "|---|---|---|---|---|---|"]
    for k in ("S&P 500", "Nasdaq", "Small caps", "VIX", "10y yield", "Dollar", "Crude", "Gold", "HY credit"):
        m = run["macro"].get(k)
        if m:
            L.append(f"| {k} | {m['level']} | {_pct(m['1d'])} | {_pct(m['1w'])} | {_pct(m['1m'])} | {_pct(m['3m'])} |")
    L += ["", "### Desk view", run["reasoning"].get("market_view") or "_(no LLM view this run)_", ""]

    sc = run.get("scorecard", {})
    L += ["## Scorecard (every past pick vs SPY)"]
    if sc.get("n_picks"):
        L.append(f"_{sc['n_picks']} picks over {sc['n_days']} days ({sc['first']} → {sc['last']})_  ")
        for h, s in sc.get("by_horizon", {}).items():
            L.append(f"- **{h}**: n={s['n']}, hit rate {s['hit_rate_pct']}%, mean excess {_pct(s['mean_excess_pct'])} (top-5: {_pct(s['top5_mean_excess_pct'])}), t={s['t_stat']}")
        tp = sc.get("top5_portfolio", {})
        L.append(f"- **Top-5 daily portfolio**: {_pct(tp.get('cum_ret_pct'))} vs SPY {_pct(tp.get('spy_cum_ret_pct'))} → excess {_pct(tp.get('excess_pct'))}")
        L.append(f"- **Verdict:** {sc.get('verdict')}")
    else:
        L.append("_First run — nothing to score yet. Tomorrow's run starts marking today's picks against SPY._")
    L.append("")

    pb = run.get("paper") or {}
    L += ["## Paper portfolio (real order records, fixed rules)", ""]
    if pb.get("enabled"):
        L += [f"_Started {pb['started']} with ${pb['start_equity']:,.0f} · rules v{pb['rules_version']}: core 40% (top five Buys, weekly rebalance) / weekly sleeve 30% (5-day holds) / SPY 30% · no margin_", "",
              f"**Account:** ${pb['equity']:,.0f} ({_pct(pb['total_return_pct'])}, ${pb['total_pnl']:+,.0f}) · cash ${pb['cash']:,.0f} · **SPY sleeve {_pct(pb.get('spy_return_pct'))}** over the same period", "",
              "| sleeve | allocated | deployed | P&L | on allocated | on deployed | open / closed lots | win rate |", "|---|---|---|---|---|---|---|---|"]
        for k in ("core", "weekly", "spy"):
            v = pb["sleeves"][k]
            L.append(f"| {k} | ${v['allocated']:,.0f} | ${v['deployed']:,.0f} | ${v['pnl']:+,.0f} | {_pct(v['return_on_allocated_pct'])} | {_pct(v.get('return_on_deployed_pct'))} | {v['open_lots']} / {v['closed_lots']} | {'—' if v['win_rate_pct'] is None else str(v['win_rate_pct']) + '%'} |")
        if pb.get("note"): L += ["", f"_{pb['note']}_"]
        L += ["", "Recent activity: " + " · ".join(x[11:] for x in pb.get("recent", [])[-6:]), "", f"_{pb['caveat']}_", ""]
    else:
        L += [f"_{pb.get('note', 'not connected')}_", ""]
    lp = run.get("learning") or {}
    L += ["## What the desk has learned (continuous learning loop)", ""]
    if lp.get("lessons_md"):
        L += [l for l in lp["lessons_md"].splitlines() if not l.startswith("# ")]
    wu = run.get("weights_used") or {}
    if wu:
        from .config import WEIGHTS as _P
        L += ["", "**Factor weights in use** (prior → effective): " + ", ".join(f"{k} {_P[k]:.2f}→{wu[k]:.2f}" if abs(wu[k] - _P[k]) > 0.004 else f"{k} {wu[k]:.2f}" for k in wu)]
    rp = lp.get("replay")
    if rp and rp.get("by_horizon"):
        m = rp["by_horizon"].get("1m", {}); w = rp.get("window", {}); tp = rp.get("top5_portfolio", {})
        L += ["", f"**Historical replay of the price screen** ({w.get('from')} → {w.get('to')}, {w.get('rebalances')} rebalances, {rp.get('distinct_names', '?')} names): "
              f"1m mean {_pct(m.get('mean_excess_pct'))} / median {_pct(m.get('median_excess_pct'))} / hit {m.get('hit_rate_pct')}% / cluster t={m.get('t_cluster')} / without top-5% {_pct(m.get('mean_excess_ex_top5pct'))}. "
              f"Top-5 portfolio {_pct(tp.get('cum_ret_pct'))} vs SPY {_pct(tp.get('spy_cum_ret_pct'))}.",
              f"_{rp.get('verdict', '')}_", "_Caveats: " + " ".join(rp.get("caveats", [])) + "_"]
    L += ["", "## Ranked picks", "",
          "| # | ticker | name | conv | verdict | driver | hold | exp. excess/yr | bear / base / bull | P(beat SPY) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for p in run["picks"]:
        r = p["rank_info"]; e = r["expected_return_pct"]
        L.append(f"| {p['rank']} | **{p['ticker']}** | {p['name'][:26]} | {p['final_conviction']} | {p['reasoning']['verdict']} | {r['driver']} | {r['horizon']} | {_pct(r['expected_excess_annual_pct'])} | {_pct(e['bear'])} / {_pct(e['base'])} / {_pct(e['bull'])} | {r['p_beat_spy']}% |")
    L += ["", "_Scenario range = base ± 1σ over the holding period (≈68% band). Expected excess is derived from long-run factor premia × this name's exposure — modest by design, because that is what the evidence supports._", ""]
    if run["reasoning"].get("top_pick_rationale"):
        L += ["**Why #1:** " + run["reasoning"]["top_pick_rationale"], ""]

    wk = run.get("weekly") or []
    L += ["## Weekly tactical sleeve — 5-trading-day holds (paper-first)", "",
          "_Highest-noise mode in the desk. Our own replay of the price screen at 1 week: hit 47%, median −0.38%, cluster t 0.37 — no edge. "
          "These use the only short-horizon signals with any support (post-earnings drift from real EPS surprises, reversal guard, revisions, flagged earnings events). "
          "Scored separately on the scorecard at 1 week; nothing here is sized for real money until that shows an edge over 20+ weeks._", ""]
    sc_w = (sc.get("sleeves") or {}).get("weekly")
    if sc_w and sc_w.get("by_horizon", {}).get("1w"):
        w1 = sc_w["by_horizon"]["1w"]
        L += [f"**Weekly scorecard (1w):** n={w1['n']}, hit {w1['hit_rate_pct']}%, mean excess {_pct(w1['mean_excess_pct'])}, median {_pct(w1['median_excess_pct'])}, cluster t={w1.get('t_cluster')} · _{sc_w['verdict']}_", ""]
    elif sc_w:
        L += [f"_Weekly scorecard: {sc_w['n_picks']} picks logged, first 1-week outcomes arrive after 5 trading days._", ""]
    if wk:
        L += ["| # | ticker | bucket | score | 1w bear / base / bull | P(beat SPY) | why |", "|---|---|---|---|---|---|---|"]
        for w in wk:
            e = w["rank_info"]["expected_return_pct"]
            L.append(f"| {w['rank']} | **{w['ticker']}** | {w['bucket']} | {w['weekly_score']:+.2f} | {_pct(e['bear'])} / {e['base']:+.2f}% / {_pct(e['bull'])} | {w['rank_info']['p_beat_spy']}% | {'; '.join(w['reasons'])} |")
        L += ["", "_Break-even gross edge ≈ +0.1%/week (spread + slippage) before short-term tax. Sell after 5 trading days regardless; do not let a weekly pick turn into a hold._", ""]
    wx = run.get("weekly_excluded") or []
    if wx:
        L += ["_Scored but excluded as too volatile for a 5-day hold (weekly σ > 12%): " + ", ".join(f"{x['ticker']} ({x['weekly_vol_pct']:.0f}%/wk)" for x in wx) + "._", ""]
    else:
        L += ["_No names cleared the weekly bar today (score ≥ 0.05 after the reversal guard)._", ""]
    L += ["## Pick details", ""]
    for p in run["picks"]:
        r = p["rank_info"]; rs = p["reasoning"]; f = p["fundamentals"]; it = p.get("intraday") or {}
        L.append(f"### {p['rank']}. {p['ticker']} — {p['name']}  ·  {p['sector']}")
        live = f" · live {it['last']} ({_pct(it['day_pct'])} today, {it['minutes_old']:.0f} min old)" if it.get("last") else ""
        L.append(f"_Price {p['price']}{live} · evidence conviction {p['conviction']} → adj {rs.get('adjust', 0):+d} → **{p['final_conviction']}** · {rs['verdict']}_  ")
        L.append(f"**Thesis.** {rs.get('thesis', '')}  ")
        if rs.get("lenses"):
            L.append("**Lenses.** " + " ".join(f"_{k}:_ {v}" for k, v in rs["lenses"].items()) + "  ")
        L.append(f"**Key risk.** {rs.get('key_risk', '')}  ")
        L.append(f"**Would change my mind.** {rs.get('change_my_mind', '')}  ")
        con = r["contributions"]
        L.append("**Score drivers.** " + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(con.items(), key=lambda kv: -abs(kv[1])) if abs(v) > 0.005) + "  ")
        L.append(f"**Returns.** 1m {_pct(p['ret_1m'])} · 3m {_pct(p['ret_3m'])} · 6m {_pct(p['ret_6m'])} · {p['pct_52w']:.0f}% of 52w high · vol {r['annual_vol_pct']}%/yr · beta {r['beta']}  ")
        if p["kind"] == "stock":
            L.append(f"**Fundamentals.** fwd P/E {f.get('forwardPE')} · ROE {f.get('returnOnEquity')} · op margin {f.get('operatingMargins')} · rev growth {f.get('revenueGrowth')} · analysts: {f.get('recommendationKey')} ({f.get('numberOfAnalystOpinions')}), target {f.get('targetMeanPrice')} · next earnings {f.get('next_earnings')}  ")
            L.append(f"**Insiders (6mo).** buys ${f.get('insider_buy_value', 0):,.0f} ({f.get('insider_buys_n', 0)}) · sells ${f.get('insider_sell_value', 0):,.0f} ({f.get('insider_sells_n', 0)})  ")
        if p.get("gurus"):
            L.append(f"**Held by.** {', '.join(p['gurus'])}  ")
        so = p.get("social") or {}
        if so.get("n"):
            L.append(f"**StockTwits.** {so['n']} msgs, bull ratio {so.get('bull_ratio')}  ")
        if p.get("risk_flags"):
            L.append(f"**Flags.** {'; '.join(p['risk_flags'])}  ")
        L.append(f"**News** ({p['news']['n']}, {p['news']['fresh_24h']} in 24h, sentiment {p['news']['sent_score']:+.2f}):")
        for n in p["news"]["items"][:5]:
            L.append(f"- [{n['source']}, {n['age_h']}h] [{n['title']}]({n['link']})")
        L.append("")

    L += ["## Guru 13F moves (stale by law — context, not signal)", ""]
    for g, d in run["gurus"].items():
        if "top" not in d:
            L.append(f"- {g}: _{d.get('error', 'unavailable')}_"); continue
        L.append(f"**{g}** — as of {d['period']} (filed {d['filed']}, {d['days_since_period']}d stale), {d['positions']} positions, ${d['aum_13f']}B  ")
        L.append(f"  top: {', '.join(f'{h['issuer'][:20]} {h['pct']}%' for h in d['top'][:6])}  ")
        if d["new"]: L.append(f"  NEW: {', '.join(d['new'][:6])}  ")
        if d["adds"]: L.append(f"  adds: {', '.join(f'{a} +{p}%' for a, p in d['adds'][:5])}  ")
        if d["trims"]: L.append(f"  trims: {', '.join(f'{a} {p}%' for a, p in d['trims'][:5])}  ")
        if d["exited"]: L.append(f"  exited: {', '.join(d['exited'][:6])}  ")
        L.append("")
    if run["reasoning"].get("what_i_ignored"):
        L += ["## What the desk deliberately discounted", run["reasoning"]["what_i_ignored"], ""]
    L += ["## Market headlines", ""] + [f"- [{n['source']}, {n['age_h']}h] [{n['title']}]({n['link']})" for n in run["market_news"][:15]]
    L += ["", "---", f"_{DISCLAIMER}_"]
    md.write_text("\n".join(L))
    (REPORTS / "latest.md").write_text(md.read_text())
    return str(md), str(js)
