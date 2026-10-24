"""Reasoning layer: a headless Claude session reads the full evidence pack and writes an independent
thesis, investor-lens views, key risk, and a BOUNDED conviction adjustment per name.
Bounded = the desk is informed by news/gurus/social but anchored to evidence; it can reorder close
names, it cannot promote a weak name to #1 on narrative alone. Degrades to rule-based text if no CLI."""
from __future__ import annotations
import glob, json, os, re, subprocess, shutil
from pathlib import Path
from .config import LENSES, learned, DATA

LESSONS = DATA / "lessons.md"


def max_adj() -> int:
    """Conviction-point cap on the reasoning layer's adjustment. 15 by default; the learning loop
    shrinks it to 10 or 5 if realized outcomes show the adjustments hurt."""
    return int(learned().get("max_adj", 15))


MAX_ADJ = 15   # default; use max_adj() at run time


def claude_bin() -> str | None:
    if os.environ.get("VANTAGE_CLAUDE_BIN"):
        return os.environ["VANTAGE_CLAUDE_BIN"]
    hits = sorted(glob.glob(os.path.expanduser("~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")))
    if hits:
        return hits[-1]
    found = shutil.which("claude")
    if found:
        return found
    # launchd's minimal PATH misses the native installer and Apple Silicon Homebrew locations
    for cand in ("~/.local/bin/claude", "/opt/homebrew/bin/claude"):
        cand = os.path.expanduser(cand)
        if os.access(cand, os.X_OK):
            return cand
    return None


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # brace-balanced last object
    start = text.rfind("\n{")
    if start == -1:
        start = text.find("{")
    depth, i0 = 0, None
    for i in range(max(start, 0), len(text)):
        c = text[i]
        if c == "{":
            if depth == 0:
                i0 = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and i0 is not None:
                try:
                    return json.loads(text[i0:i + 1])
                except Exception:
                    i0 = None
    return None


def _pack(cands: list[dict], macro: dict, mkt_news: list[dict], gurus: dict) -> str:
    L = []
    if LESSONS.exists():
        L.append("## Your track record — self-review (read before deciding)")
        L.append(LESSONS.read_text()[:4000])
        L.append("")
    reg = macro.get("_regime", {})
    L.append(f"## Market regime: volatility={reg.get('volatility')} trend={reg.get('trend')}")
    for k in ("S&P 500", "Nasdaq", "Small caps", "VIX", "10y yield", "Dollar", "Crude", "Gold", "HY credit"):
        if k in macro:
            m = macro[k]; L.append(f"- {k}: {m['level']} | 1d {m['1d']}% 1w {m['1w']}% 1m {m['1m']}% 3m {m['3m']}%")
    L.append("\n## Market headlines (last 24-36h)")
    for n in mkt_news[:14]:
        L.append(f"- [{n['source']}] {n['title']}")
    L.append("\n## Guru 13F moves (quarter-end positions, filed ≤45d later — STALE by construction)")
    for g, d in gurus.items():
        if "top" not in d:
            continue
        if d.get("stale"):
            L.append(f"- {g}: STALE (latest 13F {d['days_since_period']}d old) — carries no information, ignore.")
            continue
        L.append(f"- {g} (as of {d['period']}, {d['days_since_period']}d stale): top {[(h['issuer'][:18], h['pct']) for h in d['top'][:5]]}; NEW {d['new'][:4]}; adds {d['adds'][:3]}; exited {d['exited'][:3]}")
    L.append("\n## Candidates (evidence-ranked). z = cross-sectional z-score. conviction = evidence score 0-100.")
    for c in cands:
        f = c["fundamentals"]; r = c["rank_info"]
        L.append(f"\n### {c['ticker']} — {c['name']} ({c['sector']}) | price {c['price']} | conviction {c['conviction']} | driver {r['driver']} | horizon {r['horizon']}")
        L.append(f"- factor z: mom {c['z']['momentum']:+.2f} trend {c['z']['trend']:+.2f} quality {c['z']['quality']:+.2f} value {c['z']['value']:+.2f} lowvol {c['z']['lowvol']:+.2f} insider {c['z']['insider']:+.2f} revision {c['z']['revision']:+.2f} | catalyst {c['z']['catalyst']} | context {c['z']['context']:+.2f}")
        L.append(f"- returns: 1m {c['ret_1m']:+.1f}% 3m {c['ret_3m']:+.1f}% 6m {c['ret_6m']:+.1f}% | {c['pct_52w']:.0f}% of 52w high | vol {r['annual_vol_pct']}%")
        if c["kind"] == "stock":
            L.append(f"- fundamentals: fwdPE {f.get('forwardPE')} | ROE {f.get('returnOnEquity')} | op margin {f.get('operatingMargins')} | rev growth {f.get('revenueGrowth')} | D/E {f.get('debtToEquity')} | mcap {f.get('marketCap')} | short% {f.get('shortPercentOfFloat')} | analysts {f.get('recommendationKey')} ({f.get('numberOfAnalystOpinions')}) target {f.get('targetMeanPrice')} | next earnings {f.get('next_earnings')}")
            L.append(f"- insiders 6mo: buys ${f.get('insider_buy_value', 0):,.0f} ({f.get('insider_buys_n', 0)}) / sells ${f.get('insider_sell_value', 0):,.0f} ({f.get('insider_sells_n', 0)})")
            if f.get("longBusinessSummary"):
                L.append(f"- business: {f['longBusinessSummary'][:300]}")
        if c.get("gurus"):
            L.append(f"- held by: {', '.join(c['gurus'])}")
        so = c.get("social", {})
        if so.get("n"):
            L.append(f"- stocktwits: {so['n']} msgs, bull ratio {so.get('bull_ratio')}")
        L.append(f"- news ({c['news']['n']}, {c['news']['fresh_24h']} in 24h, sentiment {c['news']['sent_score']:+.2f}):")
        for n in c["news"]["items"][:6]:
            L.append(f"    · [{n['source']}, {n['age_h']}h] {n['title']}")
        if c.get("risk_flags"):
            L.append(f"- risk flags: {'; '.join(c['risk_flags'])}")
    return "\n".join(L)


PROMPT = """You are the head of research at Vantage, an evidence-first investment desk. You have the full evidence
pack below for today's candidate list. Your job is to DECIDE independently, not to parrot the sources.

Rules you actually follow:
- News, social sentiment, and guru 13Fs are CONTEXT. They inform you; they never decide for you. Guru positions
  are up to 4.5 months stale — treat them as "smart money was here", never "buy now".
- Anchor on the evidence factors (momentum, quality, value, insider buying, trend). Your adjustment is bounded
  to ±{max_adj} conviction points: reorder close names, flag traps, reward genuinely under-appreciated setups.
- Be concrete and falsifiable. Say what would change your mind. No hedging mush, no hype.
- Review your own track record (top of the pack) first. Where your past adjustments hurt, adjust less in that
  direction; where a driver has been failing, demand more evidence for it. But patterns need 30+ outcomes —
  never over-correct on a handful of misses or get confident on a handful of wins.
- Scenario ranges and P(beat SPY) are computed elsewhere; do NOT invent return numbers.
- Think through each investor lens briefly: {lenses}

Return ONLY a JSON object (in a ```json fence) with this exact shape:
{{
  "market_view": "3-4 sentences: regime, what matters this week, how it shapes positioning",
  "picks": {{
    "<TICKER>": {{
      "verdict": "Buy" | "Watch" | "Avoid",
      "adjust": <integer -{max_adj}..{max_adj}>,
      "thesis": "2-4 sentences, your independent view of why this ranks where it does",
      "lenses": {{"Buffett": "1 sentence", "Ackman": "1 sentence", "Burry": "1 sentence", "Druckenmiller": "1 sentence", "Damodaran": "1 sentence"}},
      "key_risk": "the single thing most likely to make this wrong",
      "change_my_mind": "the observable that would flip the verdict"
    }}
  }},
  "top_pick_rationale": "why the #1 name after your adjustments deserves it, 2-3 sentences",
  "what_i_ignored": "sources or narratives you deliberately discounted, and why"
}}

Cover EVERY candidate ticker in "picks".

=== EVIDENCE PACK ===
{pack}
"""


def reason(cands: list[dict], macro: dict, mkt_news: list[dict], gurus: dict, model: str | None = None,
           timeout: int = 420, use_llm: bool = True) -> dict:
    binp = claude_bin() if use_llm else None
    out = {"source": "rule-based", "picks": {}, "market_view": "", "raw_chars": 0}
    cap = max_adj()
    if binp:
        pack = _pack(cands, macro, mkt_news, gurus)
        prompt = PROMPT.format(max_adj=cap, lenses="; ".join(LENSES), pack=pack)
        out["pack_chars"] = len(pack); out["max_adj"] = cap
        cmd = [binp, "-p", "--output-format", "json"]
        if model or os.environ.get("VANTAGE_MODEL"):
            cmd += ["--model", model or os.environ["VANTAGE_MODEL"]]
        try:
            env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(Path.home()))
            text = r.stdout
            try:
                j = json.loads(text); text = j.get("result", text)
                out["cost_usd"] = j.get("total_cost_usd"); out["model"] = j.get("model")
            except Exception:
                pass
            parsed = _extract_json(text)
            if parsed and "picks" in parsed:
                out.update(parsed); out["source"] = "claude"; out["raw_chars"] = len(text)
        except subprocess.TimeoutExpired:
            out["error"] = "claude timeout"
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    # rule-based fallback / fill gaps
    for c in cands:
        t = c["ticker"]
        if t in out["picks"]:
            p = out["picks"][t]
            try:
                p["adjust"] = int(max(-cap, min(cap, int(p.get("adjust", 0)))))
            except Exception:
                p["adjust"] = 0
            continue
        r = c["rank_info"]; con = r["contributions"]
        top = sorted(con.items(), key=lambda kv: -kv[1])[:2]
        out["picks"][t] = {"verdict": "Buy" if c["conviction"] >= 70 else ("Watch" if c["conviction"] >= 55 else "Avoid"),
                           "adjust": 0,
                           "thesis": f"Ranks on {top[0][0]} and {top[1][0]} ({r['driver']}-driven, {r['horizon']}). "
                                     f"{'Insider buying supports it. ' if con.get('insider',0)>0.05 else ''}"
                                     f"{'Trend and momentum aligned. ' if con.get('momentum',0)>0 and con.get('trend',0)>0 else ''}",
                           "lenses": {}, "key_risk": "; ".join(c.get("risk_flags", [])[:2]) or "general market beta",
                           "change_my_mind": "loss of trend (close below 200d MA) or a negative earnings revision", "_fallback": True}
    return out
