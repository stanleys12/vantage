#!/usr/bin/env python3
"""Vantage dashboard — localhost:8866. Reads reports/latest.json; live intraday refresh on demand."""
from __future__ import annotations
import json, subprocess, sys, threading
from pathlib import Path
from flask import Flask, jsonify, render_template_string, request
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vantage.config import REPORTS, DATA, DISCLAIMER
from vantage import data as D
from vantage import charts

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
_run_lock = threading.Lock()
_run_state = {"running": False, "last": None}


def latest():
    p = REPORTS / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


@app.get("/api/latest")
def api_latest():
    return jsonify(latest() or {})


@app.get("/api/intraday")
def api_intraday():
    r = latest() or {}
    out = {p["ticker"]: D.intraday(p["ticker"]) for p in r.get("picks", [])}
    out["SPY"] = D.intraday("SPY")
    return jsonify(out)


@app.get("/api/scorecard")
def api_scorecard():
    p = DATA / "scorecard.json"
    return jsonify(json.loads(p.read_text()) if p.exists() else {})


@app.get("/api/learning")
def api_learning():
    r = latest() or {}
    out = dict(r.get("learning") or {})
    out["weights_used"] = r.get("weights_used")
    from vantage.config import WEIGHTS
    out["weights_prior"] = WEIGHTS
    return jsonify(out)


@app.get("/api/chart")
def api_chart():
    return jsonify(charts.series((request.args.get("ticker") or "").upper(), (request.args.get("range") or "1m").lower()))


@app.get("/api/paper")
def api_paper():
    return jsonify((latest() or {}).get("paper") or {})


@app.post("/api/run")
def api_run():
    if _run_state["running"]:
        return jsonify({"ok": False, "msg": "already running"})
    def go():
        with _run_lock:
            _run_state["running"] = True
            try:
                subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "run_daily.py")], cwd=str(ROOT), timeout=1800)
            finally:
                _run_state["running"] = False
    threading.Thread(target=go, daemon=True).start()
    return jsonify({"ok": True, "msg": "run started (5-10 min)"})


@app.get("/api/status")
def api_status():
    return jsonify(_run_state)


@app.get("/brief.md")
def brief_md():
    p = REPORTS / "latest.md"
    return (p.read_text() if p.exists() else "no brief yet"), 200, {"Content-Type": "text/plain; charset=utf-8"}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Vantage</title>
<style>
:root{--bg:#0b0f14;--card:#121821;--line:#1f2a36;--tx:#e6edf3;--mut:#8b98a5;--g:#3fb950;--r:#f85149;--y:#d29922;--b:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:14px 22px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}.pill{padding:3px 9px;border-radius:12px;background:#1a2330;color:var(--mut);font-size:12px}
main{padding:18px 22px;max-width:1400px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.v{font-size:20px;font-weight:600;margin-top:2px}
.pos{color:var(--g)}.neg{color:var(--r)}.mut{color:var(--mut)}
table{width:100%;border-collapse:collapse}th{text-align:left;color:var(--mut);font-weight:500;font-size:12px;padding:8px 6px;border-bottom:1px solid var(--line)}
td{padding:9px 6px;border-bottom:1px solid var(--line);vertical-align:top}tr.pick{cursor:pointer}tr.pick:hover{background:#151d28}
.conv{display:inline-block;min-width:34px;text-align:center;padding:2px 6px;border-radius:6px;font-weight:600}
.Buy{color:var(--g)}.Watch{color:var(--y)}.Avoid{color:var(--r)}
.det{display:none;background:#0e141b}.det.open{display:table-row}.det td{padding:14px 18px}
.bar{display:flex;gap:3px;flex-wrap:wrap;margin:6px 0}.seg{padding:2px 7px;border-radius:5px;font-size:11px;background:#1a2330}
.seg.p{background:#12331f;color:var(--g)}.seg.n{background:#3a1519;color:var(--r)}
h2{font-size:15px;margin:22px 0 10px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
a{color:var(--b);text-decoration:none}a:hover{text-decoration:underline}
button{background:#1f6feb;color:#fff;border:0;padding:7px 12px;border-radius:7px;cursor:pointer;font-size:13px}button:disabled{opacity:.5}
.small{font-size:12px}.news li{margin:3px 0}.lens b{color:var(--mut);font-weight:500}
.disc{margin-top:26px;padding:12px;border:1px dashed var(--line);border-radius:8px;color:var(--mut);font-size:12px}
.live{font-variant-numeric:tabular-nums}
.rangebar{display:flex;align-items:center;gap:10px;margin:22px 0 -8px;flex-wrap:wrap}
.rangebar-label{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.rangebar-group{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.rangebar button{background:transparent;color:var(--mut);border:0;border-radius:0;padding:5px 12px;font-size:12px;font-weight:600}
.rangebar button:hover{background:#151d28}.rangebar button[aria-pressed="true"]{background:#1f6feb;color:#fff}
.rangebar-hint{color:var(--mut);font-size:12px}
.vz{margin:0 0 12px;transition:opacity .15s}.vz-loading{opacity:.5}
.vz-head{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;margin-bottom:2px}
.vz-price{font-size:20px;font-weight:600}.vz-chg{font-size:13px}.vz-up{color:#0ca30c}.vz-down{color:#d03b3b}.vz-note,.vz-msg{color:var(--mut);font-size:12px}
.vz-plot{position:relative}.vz-plot svg{display:block;outline:none}.vz-plot svg:focus-visible{outline:1px solid var(--b);outline-offset:2px;border-radius:4px}
.vz-axis{font:11px -apple-system,Segoe UI,Roboto,sans-serif;fill:var(--mut)}.vz-end{font:600 12px -apple-system,Segoe UI,Roboto,sans-serif;fill:var(--tx)}
.vz-tip{position:absolute;display:none;pointer-events:none;background:#1a2330;border:1px solid #2b3948;border-radius:6px;padding:6px 9px;font-size:12px;color:var(--mut);white-space:nowrap;z-index:2}
.vz-tip b{display:block;font-size:14px;color:var(--tx)}.vz-tip span{display:block}
.vz-table{margin-top:4px;font-size:12px;color:var(--mut)}.vz-table summary,#paper summary{cursor:pointer}
.vz-scroll{max-height:180px;overflow:auto;margin-top:6px;max-width:340px}.vz-table td,.vz-table th{padding:3px 8px;border-bottom:1px solid var(--line)}
#paper pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,monospace;color:var(--mut);margin:6px 0 0}
</style></head><body>
<header><h1>Vantage</h1><span class="pill" id="date"></span><span class="pill" id="regime"></span><span class="pill" id="src"></span>
<span style="flex:1"></span><button onclick="refreshLive()" id="lb">↻ live prices</button><button onclick="runNow()" id="rb">▶ run desk now</button>
<a class="pill" href="/brief.md" target="_blank">brief.md</a></header>
<main>
<div class="grid" id="macro"></div>
<div class="card" id="view"></div>
<h2>Scorecard — every past pick vs SPY</h2><div class="card" id="score"></div>
<h2>Paper portfolio <span class="mut" style="text-transform:none">(real order records in a dedicated paper account · fixed rules · no margin)</span></h2><div class="card small" id="paper"></div>
<h2>What the desk has learned <span class="mut" style="text-transform:none">(continuous learning loop — conservative by design)</span></h2><div class="card small" id="learn"></div>
<div class="rangebar" id="rangebar"></div>
<h2>Ranked picks</h2><div class="card"><table id="picks"><thead><tr><th>#</th><th>ticker</th><th>name</th><th>conv</th><th>verdict</th><th>driver</th><th>hold</th><th>exp. excess/yr</th><th>bear / base / bull</th><th>P(beat SPY)</th><th class="live">live</th></tr></thead><tbody></tbody></table></div>
<h2>Weekly tactical sleeve <span class="mut" style="text-transform:none">(5-day holds · paper-first · highest-noise mode: our replay says 47% hit / t 0.37 at 1 week)</span></h2><div class="card"><div id="wsc" class="small mut" style="margin-bottom:6px"></div><table id="weekly"><thead><tr><th>#</th><th>ticker</th><th>bucket</th><th>score</th><th>1w bear / base / bull</th><th>P(beat)</th><th>why</th></tr></thead><tbody></tbody></table></div>
<h2>Watchlist (ranked below the cut)</h2><div class="card small" id="watch"></div>
<h2>Guru 13F moves <span class="mut" style="text-transform:none">(quarter-end, filed ≤45d later — stale by law; context not signal)</span></h2><div class="card small" id="gurus"></div>
<h2>Market headlines</h2><div class="card small news" id="mkt"></div>
<div class="disc" id="disc"></div>
</main>
<script src="/static/chart.js"></script>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f=(x,d=1)=>x==null?'—':(x>0?'+':'')+Number(x).toFixed(d)+'%';const cls=x=>x>0?'pos':(x<0?'neg':'mut');
let R=null;
async function load(){R=await (await fetch('/api/latest')).json();if(!R.date){document.getElementById('view').textContent='No brief yet — click "run desk now".';return}
document.getElementById('date').textContent=R.date+' · '+R.generated_at.replace('T',' ');const rg=R.macro._regime||{};
document.getElementById('regime').textContent=rg.volatility+' vol · '+rg.trend;document.getElementById('src').textContent='reasoning: '+(R.reasoning.source||'—')+(R.reasoning.cost_usd?` ($${R.reasoning.cost_usd.toFixed(2)})`:'');
const M=document.getElementById('macro');M.innerHTML='';for(const k of ['S&P 500','Nasdaq','Small caps','VIX','10y yield','Dollar','Crude','Gold','HY credit']){const m=R.macro[k];if(!m)continue;
M.innerHTML+=`<div class="card"><div class="k">${k}</div><div class="v">${m.level}</div><div class="small"><span class="${cls(m['1d'])}">${f(m['1d'])}</span> 1d · <span class="${cls(m['1m'])}">${f(m['1m'])}</span> 1m · <span class="${cls(m['3m'])}">${f(m['3m'])}</span> 3m</div></div>`}
document.getElementById('view').innerHTML='<div class="k">Desk view</div><div style="margin-top:6px">'+(R.reasoning.market_view||'<span class="mut">(rule-based run — no LLM view)</span>')+'</div>'+(R.reasoning.top_pick_rationale?'<div style="margin-top:8px"><b>Why #1:</b> '+R.reasoning.top_pick_rationale+'</div>':'');
const S=R.scorecard||{};let sh='';if(S.n_picks){sh=`<div class="small mut">${S.n_picks} picks over ${S.n_days} days (${S.first} → ${S.last})</div><div class="grid" style="margin:10px 0 6px">`;
for(const [h,s] of Object.entries(S.by_horizon||{}))sh+=`<div class="card"><div class="k">${h} · n=${s.n}</div><div class="v ${cls(s.mean_excess_pct)}">${f(s.mean_excess_pct,2)}</div><div class="small">hit ${s.hit_rate_pct}% · top-5 ${f(s.top5_mean_excess_pct,2)} · t=${s.t_stat??'—'}</div></div>`;
const t=S.top5_portfolio||{};sh+=`<div class="card"><div class="k">top-5 daily portfolio</div><div class="v ${cls(t.excess_pct)}">${f(t.excess_pct,2)}</div><div class="small">${f(t.cum_ret_pct,2)} vs SPY ${f(t.spy_cum_ret_pct,2)}</div></div></div><div><b>Verdict:</b> ${S.verdict}</div>`}
else sh='<span class="mut">First run — nothing to score yet. From tomorrow, every pick is marked against SPY at 1w / 1m / 3m.</span>';document.getElementById('score').innerHTML=sh;
const T=document.querySelector('#picks tbody');T.innerHTML='';for(const p of R.picks){const r=p.rank_info,e=r.expected_return_pct,rs=p.reasoning;
T.innerHTML+=`<tr class="pick" onclick="tog('${p.ticker}')"><td>${p.rank}</td><td><b>${p.ticker}</b></td><td>${p.name}<div class="small mut">${p.sector}</div></td><td><span class="conv" style="background:${p.final_conviction>=65?'#12331f':(p.final_conviction>=50?'#3a2e12':'#3a1519')}">${p.final_conviction}</span> <span class="small mut">(${p.conviction}${rs.adjust?(rs.adjust>0?'+':'')+rs.adjust:''})</span></td><td class="${rs.verdict}">${rs.verdict}</td><td>${r.driver}</td><td>${r.horizon}</td><td class="${cls(r.expected_excess_annual_pct)}">${f(r.expected_excess_annual_pct)}</td><td><span class="neg">${f(e.bear)}</span> / <b>${f(e.base)}</b> / <span class="pos">${f(e.bull)}</span></td><td>${r.p_beat_spy}%</td><td class="live" id="live-${p.ticker}">${p.intraday&&p.intraday.last?p.intraday.last+' <span class="'+cls(p.intraday.day_pct)+'">'+f(p.intraday.day_pct)+'</span><div class="small mut">'+Math.round(p.intraday.minutes_old)+'m old</div>':'—'}</td></tr>
<tr class="det" id="d-${p.ticker}"><td colspan="11">${detail(p)}</td></tr>`}
const W=document.querySelector('#weekly tbody');W.innerHTML='';for(const w of (R.weekly||[])){const e=w.rank_info.expected_return_pct;W.innerHTML+=`<tr class="pick" onclick="togw('${w.ticker}')"><td>${w.rank}</td><td><b>${w.ticker}</b><div class="small mut">${w.name}</div></td><td class="${w.bucket==='event'?'neg':''}">${w.bucket}</td><td>${w.weekly_score>0?'+':''}${w.weekly_score.toFixed(2)}</td><td><span class="neg">${f(e.bear)}</span> / <b>${e.base>0?'+':''}${e.base.toFixed(2)}%</b> / <span class="pos">${f(e.bull)}</span></td><td>${w.rank_info.p_beat_spy}%</td><td class="small">${w.reasons.join('; ')}</td></tr><tr class="det" id="dw-${w.ticker}"><td colspan="7"><div class="vz" data-ticker="${w.ticker}"></div></td></tr>`}
if(!(R.weekly||[]).length)W.innerHTML='<tr><td colspan="7" class="mut">No names cleared the weekly bar today.</td></tr>';
const sw=((R.scorecard||{}).sleeves||{}).weekly;document.getElementById('wsc').textContent=sw?(sw.by_horizon&&sw.by_horizon['1w']?`weekly scorecard (1w): n=${sw.by_horizon['1w'].n}, hit ${sw.by_horizon['1w'].hit_rate_pct}%, mean ${f(sw.by_horizon['1w'].mean_excess_pct,2)}, median ${f(sw.by_horizon['1w'].median_excess_pct,2)}, cluster t=${sw.by_horizon['1w'].t_cluster} — ${sw.verdict}`:`${sw.n_picks} weekly picks logged; first 1-week outcomes after 5 trading days`):'no weekly picks scored yet';
document.getElementById('watch').innerHTML=(R.watchlist||[]).map(w=>`<span class="seg">${w.ticker} ${w.final_conviction} <span class="${w.verdict}">${w.verdict}</span></span>`).join(' ')||'—';
let g='';for(const [n,d] of Object.entries(R.gurus||{})){if(!d.top){g+=`<div><b>${n}</b> — <span class="mut">${d.error||'unavailable'}</span></div>`;continue}
g+=`<div style="margin-bottom:9px"><b>${n}</b> <span class="mut">as of ${d.period}, filed ${d.filed}, ${d.days_since_period}d stale · ${d.positions} positions · $${d.aum_13f}B</span><div class="bar">${d.top.slice(0,8).map(h=>`<span class="seg">${h.issuer.slice(0,20)} ${h.pct}%</span>`).join('')}</div>${d.new.length?'<div><span class="pos">NEW</span> '+d.new.slice(0,6).join(', ')+'</div>':''}${d.adds.length?'<div><span class="pos">adds</span> '+d.adds.slice(0,5).map(a=>a[0]+' +'+a[1]+'%').join(', ')+'</div>':''}${d.exited.length?'<div><span class="neg">exited</span> '+d.exited.slice(0,6).join(', ')+'</div>':''}</div>`}
document.getElementById('gurus').innerHTML=g||'<span class="mut">(skipped this run)</span>';
document.getElementById('mkt').innerHTML='<ul>'+(R.market_news||[]).slice(0,15).map(n=>`<li><span class="mut">[${esc(n.source)}, ${n.age_h}h]</span> <a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join('')+'</ul>';
document.getElementById('disc').textContent=R.disclaimer||{{DISC}};loadLearning();renderPaper();}
function renderPaper(){const P=R.paper||{},el=document.getElementById('paper');if(!P.enabled){el.innerHTML='<span class="mut">'+esc(P.note||'No paper account connected. Create a NEW Alpaca paper account and put its keys in ~/vantage/.env as VANTAGE_ALPACA_KEY and VANTAGE_ALPACA_SECRET. The next morning run starts the portfolio.')+'</span>';return}
const m=v=>(v<0?'-':'')+'$'+Math.abs(Math.round(v)).toLocaleString();
let h=`<div class="mut">Started ${esc(P.started)} with ${m(P.start_equity)} · rules v${P.rules_version}: core 40% (top five Buys, weekly rebalance) / weekly sleeve 30% (5-day holds) / SPY 30%</div><div class="grid" style="margin:10px 0 6px"><div class="card"><div class="k">account</div><div class="v">${m(P.equity)}</div><div class="small"><span class="${cls(P.total_return_pct)}">${f(P.total_return_pct,2)}</span> · ${m(P.total_pnl)} · cash ${m(P.cash)}</div></div>`;
for(const k of ['core','weekly','spy']){const s=P.sleeves[k];h+=`<div class="card"><div class="k">${k==='spy'?'SPY benchmark':k+' sleeve'}</div><div class="v ${cls(s.pnl)}">${m(s.pnl)}</div><div class="small">${f(k==='spy'?P.spy_return_pct:s.return_on_deployed_pct,2)} on deployed · ${s.open_lots} open / ${s.closed_lots} closed${s.win_rate_pct!=null?' · '+s.win_rate_pct+'% wins':''}</div></div>`}
h+='</div>';
if((P.open_positions||[]).length)h+='<details><summary>Open positions ('+P.open_positions.length+')</summary><table><thead><tr><th>ticker</th><th>sleeve</th><th>opened</th><th>entry</th><th>last</th><th>P&amp;L</th></tr></thead><tbody>'+P.open_positions.map(o=>`<tr><td><b>${esc(o.ticker)}</b></td><td>${esc(o.sleeve)}</td><td>${esc(o.opened)}</td><td>${o.entry!=null?o.entry.toFixed(2):'—'}</td><td>${o.last!=null?o.last.toFixed(2):'—'}</td><td class="${cls(o.pnl)}">${m(o.pnl)}</td></tr>`).join('')+'</tbody></table></details>';
h+='<details style="margin-top:6px"><summary>Recent activity</summary><pre>'+esc((P.recent||[]).join('\n'))+'</pre></details>';
if(P.note)h+='<div style="margin-top:6px">'+esc(P.note)+'</div>';
h+='<div class="mut" style="margin-top:6px">'+esc(P.caveat)+'</div>';el.innerHTML=h}
async function loadLearning(){const L=await (await fetch('/api/learning')).json();const el=document.getElementById('learn');if(!L||!L.summary){el.innerHTML='<span class="mut">no learning data yet</span>';return}
let h='';const s=L.summary,a=L.attribution||{},ld=L.learned||{},wp=L.weights_prior||{},wu=L.weights_used||{};
h+=`<div><b>Scored:</b> ${s.n_scored} picks over ${a.n_dates??0} independent dates · λ=${s.lambda} · ${s.changes&&s.changes.length?'<span class="pos">changes: '+s.changes.join('; ')+'</span>':'<span class="mut">'+(s.note||'no changes')+'</span>'}</div>`;
if(a.overall){const o=a.overall;h+=`<div style="margin-top:6px"><b>1-month outcomes:</b> mean <span class="${cls(o.mean_excess_pct)}">${f(o.mean_excess_pct,2)}</span> · median <span class="${cls(o.median_excess_pct)}">${f(o.median_excess_pct,2)}</span> · hit ${o.hit_rate_pct}% · cluster t=${o.t_cluster??'—'} · without top-5% winners ${o.mean_excess_ex_top5pct!=null?f(o.mean_excess_ex_top5pct,2):'—'}</div><div><b>Verdict:</b> ${a.verdict||''}</div>`;
if(a.by_driver)h+=`<div style="margin-top:6px"><b>By driver:</b> ${Object.entries(a.by_driver).map(([k,v])=>`<span class="seg ${v.mean_excess_pct>0?'p':'n'}">${k} ${f(v.mean_excess_pct)} (med ${f(v.median_excess_pct)}, n=${v.n})</span>`).join(' ')}</div>`;
if(a.by_conviction)h+=`<div><b>By conviction:</b> ${Object.entries(a.by_conviction).map(([k,v])=>`<span class="seg ${v.mean_excess_pct>0?'p':'n'}">${k}: ${f(v.mean_excess_pct)} (n=${v.n})</span>`).join(' ')} ${a.calibration_corr!=null?'· calibration corr '+a.calibration_corr:''}</div>`;
if(a.llm_adjust){const la=a.llm_adjust;h+=`<div><b>Reasoning-layer adjustments:</b> pushed up ${la.mean_excess_up!=null?f(la.mean_excess_up):'—'} (n=${la.n_up}) · pushed down ${la.mean_excess_down!=null?f(la.mean_excess_down):'—'} (n=${la.n_down}) · t=${la.t_diff??'—'} · cap ±${ld.max_adj??15}</div>`}}
h+=`<div style="margin-top:8px"><b>Factor weights</b> (prior → in use): <div class="bar">${Object.keys(wp).map(k=>`<span class="seg ${Math.abs((wu[k]||0)-wp[k])>0.004?(wu[k]>wp[k]?'p':'n'):''}">${k} ${wp[k].toFixed(2)}${Math.abs((wu[k]||0)-wp[k])>0.004?' → '+wu[k].toFixed(2):''}</span>`).join('')}</div></div>`;
const rp=L.replay;if(rp){const m=(rp.by_horizon||{})['1m']||{},c=(rp.concentration||{})['1m']||{};h+=`<div style="margin-top:8px"><b>Historical replay of the price screen</b> <span class="mut">(${rp.window.from} → ${rp.window.to}, ${rp.window.rebalances} rebalances, ${rp.distinct_names} names)</span><div>1m: mean <span class="${cls(m.mean_excess_pct)}">${f(m.mean_excess_pct,2)}</span> · median <span class="${cls(m.median_excess_pct)}">${f(m.median_excess_pct,2)}</span> · hit ${m.hit_rate_pct}% · cluster t=${m.t_cluster} · without top-5% ${f(m.mean_excess_ex_top5pct,2)} · top-5 portfolio ${f(rp.top5_portfolio.cum_ret_pct)} vs SPY ${f(rp.top5_portfolio.spy_cum_ret_pct)}</div>${c.top5_names?'<div>Excess concentrated in: '+c.top5_names.map(x=>x[0]+' '+f(x[1],0)).join(', ')+' ('+Math.round(c.top5_share_of_positive_excess*100)+'% of all positive excess)</div>':''}<div><b>${rp.verdict}</b></div><div class="mut">${(rp.caveats||[]).join(' ')}</div></div>`}
if(L.lessons_md){const rc=L.lessons_md.split('## Recent resolved calls')[1];if(rc)h+=`<details style="margin-top:8px"><summary>Recent resolved calls (self-review fed to the reasoning layer)</summary><pre style="white-space:pre-wrap;font:12px/1.4 ui-monospace,monospace;color:var(--mut)">${rc.replace(/</g,'&lt;')}</pre></details>`}
el.innerHTML=h}
function detail(p){const r=p.rank_info,rs=p.reasoning,fd=p.fundamentals||{};const con=Object.entries(r.contributions).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).filter(x=>Math.abs(x[1])>0.005);
let h=`<div class="vz" data-ticker="${p.ticker}"></div><div><b>Thesis.</b> ${rs.thesis||'—'}</div>`;if(rs.lenses&&Object.keys(rs.lenses).length)h+=`<div class="lens" style="margin-top:6px">${Object.entries(rs.lenses).map(([k,v])=>`<div><b>${k}:</b> ${v}</div>`).join('')}</div>`;
h+=`<div style="margin-top:6px"><b>Key risk.</b> ${rs.key_risk||'—'}</div><div><b>Would change my mind.</b> ${rs.change_my_mind||'—'}</div>`;
h+=`<div class="bar" style="margin-top:8px">${con.map(([k,v])=>`<span class="seg ${v>0?'p':'n'}">${k} ${v>0?'+':''}${v.toFixed(2)}</span>`).join('')}</div>`;
h+=`<div class="small">1m ${f(p.ret_1m)} · 3m ${f(p.ret_3m)} · 6m ${f(p.ret_6m)} · ${p.pct_52w}% of 52w high · vol ${r.annual_vol_pct}%/yr · beta ${r.beta}</div>`;
if(p.kind==='stock')h+=`<div class="small">fwd P/E ${fd.forwardPE??'—'} · ROE ${fd.returnOnEquity??'—'} · op margin ${fd.operatingMargins??'—'} · rev growth ${fd.revenueGrowth??'—'} · analysts ${fd.recommendationKey??'—'} (${fd.numberOfAnalystOpinions??'—'}) target ${fd.targetMeanPrice??'—'} · next earnings ${fd.next_earnings??'—'}</div><div class="small">insiders 6mo: buys $${Math.round(fd.insider_buy_value||0).toLocaleString()} (${fd.insider_buys_n||0}) · sells $${Math.round(fd.insider_sell_value||0).toLocaleString()} (${fd.insider_sells_n||0})</div>`;
if(p.gurus&&p.gurus.length)h+=`<div class="small"><b>Held by:</b> ${p.gurus.join(', ')}</div>`;if(p.social&&p.social.n)h+=`<div class="small">StockTwits: ${p.social.n} msgs, bull ratio ${p.social.bull_ratio}</div>`;
if(p.risk_flags&&p.risk_flags.length)h+=`<div class="small neg">Flags: ${p.risk_flags.join('; ')}</div>`;
h+=`<div class="small" style="margin-top:6px"><b>News</b> (${p.news.n}, ${p.news.fresh_24h} in 24h, sentiment ${f(p.news.sent_score*100,0)})</div><ul class="news small">${p.news.items.slice(0,5).map(n=>`<li><span class="mut">[${esc(n.source)}, ${n.age_h}h]</span> <a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join('')}</ul>`;return h}
function openRow(id){const d=document.getElementById(id);if(!d)return null;d.classList.toggle('open');if(d.classList.contains('open'))VZ.mount(d.querySelector('.vz'));return d}
function tog(t){return openRow('d-'+t)}
function togw(t){return openRow('dw-'+t)}
async function refreshLive(){const b=document.getElementById('lb');b.disabled=true;b.textContent='…';const j=await (await fetch('/api/intraday')).json();
for(const [t,i] of Object.entries(j)){const el=document.getElementById('live-'+t);if(el&&i.last)el.innerHTML=i.last+' <span class="'+cls(i.day_pct)+'">'+f(i.day_pct)+'</span><div class="small mut">'+Math.round(i.minutes_old)+'m old</div>'}b.disabled=false;b.textContent='↻ live prices'}
async function runNow(){const b=document.getElementById('rb');b.disabled=true;const j=await (await fetch('/api/run',{method:'POST'})).json();b.textContent=j.msg;
const iv=setInterval(async()=>{const s=await (await fetch('/api/status')).json();if(!s.running){clearInterval(iv);b.disabled=false;b.textContent='▶ run desk now';load()}},15000)}
VZ.rangeBar(document.getElementById('rangebar'));
load().then(()=>{const o=(new URLSearchParams(location.search).get('open')||'').toUpperCase();if(!o)return;const d=tog(o)||togw(o);if(d)d.previousElementSibling.scrollIntoView({block:'start'})});setInterval(refreshLive,120000);
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE.replace("{{DISC}}", json.dumps(DISCLAIMER)))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8866, debug=False)
