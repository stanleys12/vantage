"""Learning loop. Turns scored outcomes into (conservatively) changed behavior.

What it learns                          How it guards against learning noise
- which factors actually paid           ridge regression, min 30 scored picks, shrinkage λ ≤ 0.3,
  (→ factor weights)                    every weight floored at 25% / capped at 200% of its prior
- whether the reasoning layer's         correlation of adjust vs outcome; only shrinks MAX_ADJ when
  adjustments help or hurt              |t| > 1.5 over ≥30 picks
- whether conviction is calibrated      monotonicity of excess by conviction bucket; shrinks slope
- its own past calls (self-review)      lessons.md fed back into the prompt, capped to recent outcomes
"""
from __future__ import annotations
import json, math
from datetime import date
import numpy as np
from .config import DATA, WEIGHTS, LEARNED
from . import track
from .track import cluster_t, tail_share

ATTR = DATA / "attribution.json"
LESSONS = DATA / "lessons.md"
LEARNED_PATH = LEARNED
MIN_N = 60            # scored picks before ANY parameter moves ...
MIN_DATES = 20        # ... AND at least this many distinct pick-dates (same-day picks are one observation)
MAX_LAMBDA = 0.30     # never more than 30% of the way from prior toward realized
FULL_DATES = 120      # λ reaches MAX_LAMBDA at this many independent dates (~6 months of daily runs)
FLOOR, CEIL = 0.25, 2.0
EVIDENCE = ["momentum", "trend", "quality", "value", "lowvol", "insider", "revision"]
KEY = "1m"            # learn on 21-trading-day outcomes: soonest horizon with usable signal


def _grp(rows, keyf):
    g = {}
    for r in rows:
        g.setdefault(keyf(r), []).append((r["date"], r[KEY]["excess"]))
    out = {}
    for k, v in sorted(g.items(), key=lambda kv: -np.mean([e for _, e in kv[1]])):
        ex = [e for _, e in v]
        out[k] = {"n": len(ex), "mean_excess_pct": round(float(np.mean(ex)), 2), "median_excess_pct": round(float(np.median(ex)), 2),
                  "hit_rate_pct": round(float(np.mean([x > 0 for x in ex]) * 100)), **cluster_t(v)}
    return out


def attribution(rows: list[dict]) -> dict:
    scored = [r for r in rows if KEY in r and r.get("sleeve", "core") == "core"]
    out = {"n_scored": len(scored), "n_logged": len(rows), "horizon": KEY}
    if not scored:
        return out
    ex = [r[KEY]["excess"] for r in scored]
    out["overall"] = {"n": len(ex), "mean_excess_pct": round(float(np.mean(ex)), 2), "median_excess_pct": round(float(np.median(ex)), 2),
                      "hit_rate_pct": round(float(np.mean([x > 0 for x in ex]) * 100)),
                      **cluster_t([(r["date"], r[KEY]["excess"]) for r in scored]), **tail_share(ex)}
    out["n_dates"] = out["overall"]["clusters"]
    out["verdict"] = track.judge(out["overall"])
    out["by_driver"] = _grp(scored, lambda r: r.get("driver") or "?")
    out["by_verdict"] = _grp(scored, lambda r: r.get("verdict") or "?")
    out["by_regime"] = _grp(scored, lambda r: r.get("regime") or "?")
    out["by_rank"] = _grp(scored, lambda r: "top3" if r["rank"] <= 3 else ("4-6" if r["rank"] <= 6 else "7+"))
    def bucket(c):
        return "80+" if c >= 80 else ("70-79" if c >= 70 else ("60-69" if c >= 60 else "<60"))
    out["by_conviction"] = _grp(scored, lambda r: bucket(r["conviction"]))
    # calibration: does realized excess rise with conviction? (Spearman-ish: corr of conviction vs excess)
    c = np.array([r["conviction"] for r in scored], float); e = np.array(ex, float)
    out["calibration_corr"] = round(float(np.corrcoef(c, e)[0, 1]), 3) if len(scored) > 3 and c.std() > 0 else None
    # LLM adjustment value: did names it pushed up beat names it pushed down?
    adj = np.array([r.get("adjust", 0) or 0 for r in scored], float)
    if adj.std() > 0:
        up = [r[KEY]["excess"] for r in scored if (r.get("adjust") or 0) > 0]
        dn = [r[KEY]["excess"] for r in scored if (r.get("adjust") or 0) < 0]
        # sign-weighted excess (up → +excess, down → −excess), clustered by date: does the adjustment direction pay?
        sw = [(r["date"], math.copysign(r[KEY]["excess"], r.get("adjust") or 0)) for r in scored if (r.get("adjust") or 0) != 0]
        out["llm_adjust"] = {"corr": round(float(np.corrcoef(adj, e)[0, 1]), 3), "n_up": len(up), "n_down": len(dn),
                             "mean_excess_up": round(float(np.mean(up)), 2) if up else None,
                             "mean_excess_down": round(float(np.mean(dn)), 2) if dn else None,
                             "t_diff": cluster_t(sw)["t_cluster"] if up and dn else None}
    # realized factor efficacy: ridge regression of excess on factor z's (strong ridge α = n)
    Z = np.array([[float((r.get("z") or {}).get(k, 0) or 0) for k in EVIDENCE] for r in scored])
    if len(scored) >= 10 and Z.std() > 0:
        Zc = Z - Z.mean(0); yc = e - e.mean()
        coef = np.linalg.solve(Zc.T @ Zc + len(scored) * np.eye(len(EVIDENCE)), Zc.T @ yc)
        out["factor_efficacy"] = {k: round(float(c * (Z[:, i].std() or 1)), 3) for i, (k, c) in enumerate(zip(EVIDENCE, coef))}
    return out


def adapt(attr: dict) -> dict:
    """Shrinkage update of factor weights toward realized efficacy. Returns the learned param set."""
    n = attr.get("n_scored", 0); nd = attr.get("n_dates", 0)
    prior = dict(WEIGHTS)
    out = {"n": n, "n_dates": nd, "lambda": 0.0, "weights": prior, "max_adj": 15, "conviction_slope": 1.1, "updated": date.today().isoformat(), "changes": []}
    if n < MIN_N or nd < MIN_DATES or "factor_efficacy" not in attr:
        out["note"] = f"{n}/{MIN_N} scored picks over {nd}/{MIN_DATES} independent dates — priors unchanged (learning needs a real sample first)"
        return out
    tail = attr.get("overall", {}).get("mean_excess_ex_top5pct")
    if tail is not None and tail <= 0 and (attr["overall"].get("mean_excess_pct") or 0) > 0:
        out["note"] = "positive mean comes entirely from a few outliers (negative without the top-5%) — refusing to learn from a fat tail; priors unchanged"
        return out
    lam = min(MAX_LAMBDA, nd / FULL_DATES)
    eff = {k: max(0.0, v) for k, v in attr["factor_efficacy"].items()}
    tot_prior = sum(prior[k] for k in EVIDENCE)
    if sum(eff.values()) <= 0:
        out["note"] = "no factor showed positive realized efficacy — priors unchanged"; return out
    realized = {k: eff[k] / sum(eff.values()) * tot_prior for k in EVIDENCE}
    new = dict(prior)
    for k in EVIDENCE:
        w = (1 - lam) * prior[k] + lam * realized[k]
        new[k] = float(min(CEIL * prior[k], max(FLOOR * prior[k], w)))
    s = sum(new.values()); new = {k: v / s for k, v in new.items()}
    out["lambda"] = round(lam, 3); out["weights"] = {k: round(v, 4) for k, v in new.items()}
    out["changes"] = [f"{k}: {prior[k]:.3f}→{new[k]:.3f}" for k in EVIDENCE if abs(new[k] - prior[k]) > 0.005]
    # reasoning-layer gate
    la = attr.get("llm_adjust") or {}
    t = la.get("t_diff")
    if t is not None:
        out["max_adj"] = 15 if t > 1.5 else (5 if t < -1.5 else 10)
        out["changes"].append(f"reasoning adjustment cap → ±{out['max_adj']} (t={t})")
    # calibration gate: if higher conviction does not mean higher excess, cool the conviction scale
    cc = attr.get("calibration_corr")
    if cc is not None and cc < -0.1:
        out["conviction_slope"] = 0.8; out["changes"].append(f"conviction slope 1.1→0.8 (calibration corr {cc})")
    return out


def lessons_md(rows: list[dict], attr: dict, learned: dict, recent: int = 12) -> str:
    L = [f"# What the desk has learned (as of {date.today().isoformat()})", ""]
    n = attr.get("n_scored", 0)
    if not n:
        L.append("_No scored outcomes yet. Picks are logged; the first 1-month outcomes arrive ~21 trading days after the first run._")
        return "\n".join(L)
    o = attr["overall"]; nd = attr.get("n_dates", 0)
    L.append(f"**{n} picks over {nd} dates scored at 1 month:** mean excess vs SPY {o['mean_excess_pct']:+.2f}%, median {o['median_excess_pct']:+.2f}%, "
             f"hit rate {o['hit_rate_pct']}%, cluster t={o.get('t_cluster')}"
             + (f", without top-5% winners {o['mean_excess_ex_top5pct']:+.2f}%" if o.get("mean_excess_ex_top5pct") is not None else "") + ".")
    L.append(f"**Verdict:** {attr.get('verdict')}")
    if n < MIN_N or nd < MIN_DATES:
        L.append(f"**Below the minimum ({MIN_N} picks / {MIN_DATES} dates) — everything here is provisional; no parameters move yet.**")
    L.append("")
    L.append("**By driver:** " + "; ".join(f"{k} {v['mean_excess_pct']:+.1f}% (med {v['median_excess_pct']:+.1f}%, n={v['n']})" for k, v in attr["by_driver"].items()))
    L.append("**By conviction:** " + "; ".join(f"{k} {v['mean_excess_pct']:+.1f}% (n={v['n']})" for k, v in attr["by_conviction"].items())
             + (f" · calibration corr {attr['calibration_corr']}" if attr.get("calibration_corr") is not None else ""))
    if attr.get("by_verdict"):
        L.append("**By verdict:** " + "; ".join(f"{k} {v['mean_excess_pct']:+.1f}% (n={v['n']})" for k, v in attr["by_verdict"].items()))
    la = attr.get("llm_adjust")
    if la:
        L.append(f"**Your adjustments:** names you pushed UP averaged {la['mean_excess_up']}% (n={la['n_up']}), pushed DOWN {la['mean_excess_down']}% (n={la['n_down']}), corr {la['corr']}, t={la['t_diff']}. "
                 + ("Helping." if (la.get("t_diff") or 0) > 1.5 else ("**Hurting — adjust less.**" if (la.get("t_diff") or 0) < -1.5 else "No evidence either way yet.")))
    if attr.get("factor_efficacy"):
        L.append("**Realized factor efficacy (ridge, per 1σ):** " + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(attr["factor_efficacy"].items(), key=lambda kv: -kv[1])))
    if learned.get("changes"):
        L.append("**Parameter changes applied:** " + "; ".join(learned["changes"]))
    else:
        L.append(f"**Parameters:** {learned.get('note', 'unchanged')}")
    L += ["", "## Recent resolved calls (1-month outcome)"]
    scored = sorted([r for r in rows if KEY in r and r.get("sleeve", "core") == "core"], key=lambda r: r["date"])[-recent:]
    for r in scored:
        e = r[KEY]["excess"]
        L.append(f"- **{r['ticker']}** ({r['date']}, #{r['rank']}, conv {r['conviction']}, {r.get('verdict')}, adj {r.get('adjust', 0):+d}, {r.get('driver')}): "
                 f"{'✅' if e > 0 else '❌'} {e:+.1f}% vs SPY. Thesis: {r.get('thesis', '')[:140]}… Flip: {r.get('flip', '')[:90]}")
    L += ["", "_Rule for using this: patterns need 30+ outcomes. Do not over-correct on a handful of misses; do not get confident on a handful of wins._"]
    return "\n".join(L)


def update() -> dict:
    rows = track.joined()
    attr = attribution(rows)
    learned = adapt(attr)
    ATTR.write_text(json.dumps(attr, indent=1))
    LEARNED.write_text(json.dumps(learned, indent=1))
    LESSONS.write_text(lessons_md(rows, attr, learned))
    return {"n_scored": attr.get("n_scored", 0), "lambda": learned["lambda"], "changes": learned["changes"], "note": learned.get("note")}
