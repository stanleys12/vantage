"""Weekly tactical sleeve: 5-trading-day holds. The highest-noise mode in the desk, and it says so.

Evidence at this horizon is thin: post-earnings-announcement drift (real but fading, strongest in the
first weeks after a big surprise), short-term reversal (do NOT buy last week's spikes), analyst revision
momentum. Our own 15-month replay of the price screen at 1 week: hit 47%, median -0.38%, cluster t 0.37 —
no edge. This sleeve exists to TEST weekly picks on the scorecard, paper-first, not because the evidence
says they work. Costs matter here: ~0.05% round-trip spread + short-term capital-gains tax on every win."""
from __future__ import annotations
import math
from .config import EQUITY_RISK_PREMIUM

HORIZON_YEARS = 1 / 52
BREAKEVEN_EDGE_PCT = 0.1   # gross weekly excess needed just to cover spread + slippage
MAX_WEEKLY_VOL_PCT = 12.0  # ±12% over 5 days (~85%/yr) is already 'more risk'; beyond that is a lottery ticket, not a trade


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def score(c: dict) -> tuple[float, str, list[str]]:
    f = c["fundamentals"]; z = c["z"]
    reasons, s = [], 0.0
    dse, sp = f.get("days_since_earnings"), f.get("eps_surprise_pct")
    r1w = c.get("ret_1w", 0.0)        # percent
    drift = False
    if dse is not None and dse <= 15 and sp is not None:
        if sp > 5 and c["ret_1m"] > 0:
            s += 0.15 + 0.35 * min(sp, 30) / 30; drift = True
            reasons.append(f"post-earnings drift: beat by {sp:.0f}% {dse}d ago, price confirming")
        elif sp < -5:
            s -= 0.3; reasons.append(f"missed earnings by {sp:.0f}% {dse}d ago (negative drift)")
    zm = max(min(float(z["momentum"]), 2.0), -2.0)
    if r1w > 12:
        s -= 0.35; reasons.append(f"+{r1w:.0f}% last week — reversal risk")
    elif r1w < -10 and float(z["quality"]) > 0.3:
        s += 0.15; reasons.append(f"{r1w:.0f}% last week on a quality name — bounce candidate")
    elif zm > 0.5:
        s += 0.25 * zm / 2; reasons.append("trend intact, not extended")
    zr = max(min(float(z["revision"]), 2.0), -2.0)
    s += 0.2 * zr / 2
    if zr > 0.8:
        reasons.append("analyst revisions rising")
    ctx = float(c["news"].get("sent_score") or 0); fresh = int(c["news"].get("fresh_24h") or 0)
    s += 0.1 * ctx
    if fresh >= 5 and ctx > 0.3:
        reasons.append(f"{fresh} positive headlines in 24h")
    d2e = f.get("days_to_earnings")
    if d2e is not None and 0 <= d2e <= 5:
        bucket = "event"; reasons.append(f"EARNINGS IN {d2e}d — fat tail both ways")
    else:
        bucket = "drift" if drift else "trend"
    if (f.get("shortPercentOfFloat") or 0) > 0.15:
        reasons.append(f"short interest {f['shortPercentOfFloat']*100:.0f}% (squeeze/knife both possible)")
    return round(s, 3), bucket, reasons


def rank_info_1w(c: dict, rf: float) -> dict:
    ri = c["rank_info"]
    sigma = ri["annual_vol_pct"] / 100; b = ri["beta"]; excess = ri["expected_excess_annual_pct"] / 100
    sig = sigma * math.sqrt(HORIZON_YEARS)
    base = (rf + b * EQUITY_RISK_PREMIUM + excess) * HORIZON_YEARS
    p = _phi((excess * HORIZON_YEARS) / max(0.8 * sig, 1e-6))
    return {"horizon": "1 week (5 trading days)", "horizon_years": HORIZON_YEARS,
            "expected_return_pct": {"bear": round((base - sig) * 100, 1), "base": round(base * 100, 2), "bull": round((base + sig) * 100, 1)},
            "p_beat_spy": round(p * 100), "weekly_vol_pct": round(sig * 100, 1), "breakeven_edge_pct": BREAKEVEN_EDGE_PCT,
            "expected_excess_annual_pct": ri["expected_excess_annual_pct"], "beta": b, "annual_vol_pct": ri["annual_vol_pct"],
            "driver": "weekly", "contributions": ri["contributions"],
            "note": "±1σ over 5 days; costs ≈0.05% round-trip + short-term tax on wins"}


def select(cl: list[dict], rf: float, n: int = 6, max_event: int = 2, min_score: float = 0.05) -> tuple[list[dict], list[dict]]:
    """Returns (picks, excluded). excluded = names that scored but were too volatile for a 5-day hold."""
    scored, excluded = [], []
    for c in cl:
        if c.get("kind") != "stock":
            continue
        s, b, reasons = score(c)
        scored.append((s, b, reasons, c))
    scored.sort(key=lambda x: -x[0])
    out, ev = [], 0
    for s, b, reasons, c in scored:
        if s < min_score:
            break
        ri = rank_info_1w(c, rf)
        if ri["weekly_vol_pct"] > MAX_WEEKLY_VOL_PCT:
            excluded.append({"ticker": c["ticker"], "weekly_score": s, "weekly_vol_pct": ri["weekly_vol_pct"], "bucket": b})
            continue
        if b == "event":
            if ev >= max_event:
                continue
            ev += 1
        keep = ("ticker", "name", "sector", "kind", "price", "conviction", "final_conviction", "ret_1w", "ret_1m", "ret_3m",
                "pct_52w", "z", "news", "fundamentals", "reasoning", "risk_flags", "gurus", "social", "intraday")
        out.append({**{k: c.get(k) for k in keep}, "weekly_score": s, "bucket": b, "reasons": reasons, "rank_info": ri})
        if len(out) >= n:
            break
    for i, p in enumerate(out, 1):
        p["rank"] = i
    return out, excluded
