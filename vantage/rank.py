"""Turn scores into an honest recommendation: driver, horizon, expected excess return, scenarios, P(beat SPY)."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .config import WEIGHTS, weights, HORIZON, FACTOR_PREMIUM, EQUITY_RISK_PREMIUM
from .factors import contributions

HORIZON_YEARS = {"momentum": 0.5, "trend": 0.4, "quality": 2.0, "value": 2.0, "lowvol": 1.0,
                 "insider": 0.75, "catalyst": 0.1, "revision": 0.2, "context": 0.25}
EVIDENCE = [k for k in WEIGHTS if FACTOR_PREMIUM.get(k, 0) > 0]


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def build(row: pd.Series, rf: float, beta: float | None) -> dict:
    con = contributions(row)
    z = {"momentum": row["z_momentum"], "trend": row["z_trend"], "quality": row.get("z_quality", 0),
         "value": row.get("z_value", 0), "lowvol": row["z_lowvol"], "insider": row.get("z_insider", 0),
         "revision": row.get("z_revision", 0)}
    z = {k: (0.0 if pd.isna(v) else float(v)) for k, v in z.items()}
    W = weights(); W_EV = sum(W[k] for k in EVIDENCE)
    # driver = the name's most DISTINCTIVE positive exposure (z × sqrt(weight)), not the heaviest weight —
    # otherwise every pick in a momentum-screened set is labelled "momentum".
    pos = {k: v * math.sqrt(W[k]) for k, v in z.items() if v > 0.15}
    if row.get("catalyst", 0) > 0 and (not pos or max(pos.values()) < 0.3):
        driver = "catalyst"
    else:
        driver = max(pos, key=pos.get) if pos else "momentum"
    horizon, hy = HORIZON[driver], HORIZON_YEARS[driver]
    # honest expected excess (annualized): long-run factor premia × clipped exposure
    excess = sum((W[k] / W_EV) * FACTOR_PREMIUM[k] * float(np.clip(z[k], -1.5, 1.5)) / 1.5 for k in z)
    b = 1.0 if beta is None or np.isnan(beta) else float(np.clip(beta, 0.3, 2.5))
    base_annual = rf + b * EQUITY_RISK_PREMIUM + excess
    sigma = float(row["vol_63"]) if not pd.isna(row["vol_63"]) else 0.3
    sig_h = sigma * math.sqrt(hy)
    base_h = base_annual * hy
    bear, bull = base_h - sig_h, base_h + sig_h
    # P(beat SPY over horizon): excess vs idiosyncratic vol (≈0.8σ); honest, usually 50-65%
    p_beat = _phi((excess * hy) / max(0.8 * sig_h, 1e-6))
    return {
        "driver": driver, "horizon": horizon, "horizon_years": hy,
        "expected_excess_annual_pct": round(excess * 100, 1),
        "expected_return_pct": {"bear": round(bear * 100, 1), "base": round(base_h * 100, 1), "bull": round(bull * 100, 1)},
        "p_beat_spy": round(p_beat * 100),
        "beta": round(b, 2), "annual_vol_pct": round(sigma * 100, 1),
        "contributions": con,
    }


def risk_flags(row: pd.Series, f: dict) -> list[str]:
    fl = []
    if row.get("catalyst", 0) > 0:
        fl.append(f"earnings in {f.get('days_to_earnings')}d (event risk both ways)")
    if row["vol_63"] > 0.55:
        fl.append(f"high vol ({row['vol_63']*100:.0f}%/yr)")
    if row["pct_of_52w_high"] < 0.75:
        fl.append(f"{(1-row['pct_of_52w_high'])*100:.0f}% below 52w high")
    sp = f.get("shortPercentOfFloat")
    if sp and sp > 0.10:
        fl.append(f"short interest {sp*100:.0f}% of float")
    if (f.get("insider_sell_value") or 0) > 5 * max(f.get("insider_buy_value") or 0, 1) and (f.get("insider_sells_n") or 0) >= 3:
        fl.append("net insider selling (6mo)")
    if row["ret_1m"] > 0.20:
        fl.append(f"+{row['ret_1m']*100:.0f}% in 1m — reversal risk")
    fpe = f.get("forwardPE")
    if fpe and fpe > 45:
        fl.append(f"forward P/E {fpe:.0f} — priced for perfection")
    return fl
