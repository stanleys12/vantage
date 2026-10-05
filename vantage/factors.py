"""Factor scoring. Stage 1 = price-only screen on the whole universe; Stage 2 = deep scores on candidates.
Every score is a winsorized cross-sectional z-score so the composite is comparable across names."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .config import WEIGHTS, weights, learned


def _z(s: pd.Series, clip: float = 3.0) -> pd.Series:
    s = s.astype(float)
    if s.notna().sum() < 3:
        return s * 0
    lo, hi = s.quantile(0.02), s.quantile(0.98)
    s = s.clip(lo, hi)
    sd = s.std()
    return ((s - s.mean()) / sd).clip(-clip, clip) if sd and not math.isnan(sd) else s * 0


# ---------------------------------------------------------------- stage 1
def stage1(px: pd.DataFrame, kinds: dict[str, str]) -> pd.DataFrame:
    """px: wide adjusted closes (>=260 rows). kinds: ticker->'stock'|'etf'. Returns per-ticker table."""
    px = px.dropna(axis=1, thresh=230).ffill()
    last = px.iloc[-1]
    rows = {}
    rows["mom_12_1"] = px.iloc[-22] / px.iloc[-252] - 1
    rows["ret_1w"] = last / px.iloc[-6] - 1
    rows["ret_1m"] = last / px.iloc[-22] - 1
    rows["ret_3m"] = last / px.iloc[-64] - 1
    rows["ret_6m"] = last / px.iloc[-127] - 1
    ma50, ma200 = px.rolling(50).mean().iloc[-1], px.rolling(200).mean().iloc[-1]
    rows["trend_raw"] = (last > ma50).astype(int) + (last > ma200).astype(int) + (ma50 > ma200).astype(int)
    rows["dist_200"] = last / ma200 - 1
    rets = px.pct_change().iloc[-63:]
    rows["vol_63"] = rets.std() * math.sqrt(252)
    dd = (px.iloc[-252:] / px.iloc[-252:].cummax() - 1).iloc[-1]
    rows["drawdown_1y"] = dd
    hi52 = px.iloc[-252:].max()
    rows["pct_of_52w_high"] = last / hi52
    df = pd.DataFrame(rows)
    df["kind"] = pd.Series(kinds).reindex(df.index).fillna("stock")
    df["price"] = last
    # z-scores within kind (stocks vs stocks, ETFs vs ETFs)
    for col, zcol, sign in (("mom_12_1", "z_momentum", 1), ("vol_63", "z_lowvol", -1)):
        df[zcol] = df.groupby("kind")[col].transform(_z) * sign
    tr = df["trend_raw"] + df["dist_200"].clip(-0.3, 0.3)
    df["z_trend"] = tr.groupby(df["kind"]).transform(_z)
    # short-term reversal guard: extreme 1-month spikes tend to mean-revert
    rev = (-df["ret_1m"]).groupby(df["kind"]).transform(_z)
    df["z_momentum"] = df["z_momentum"] + 0.15 * rev
    W = weights()
    df["stage1"] = W["momentum"] * df["z_momentum"] + W["trend"] * df["z_trend"] + W["lowvol"] * df["z_lowvol"]
    return df.sort_values("stage1", ascending=False)


# ---------------------------------------------------------------- stage 2
def _safe(v, default=np.nan):
    try:
        return default if v is None else float(v)
    except Exception:
        return default


def stage2(cands: pd.DataFrame, fund: dict[str, dict], newsd: dict[str, dict], socd: dict[str, dict]) -> pd.DataFrame:
    """Add fundamental/insider/revision/catalyst/context z-scores and the composite."""
    df = cands.copy()
    q, v, ins, rev, cat, ctx, note = {}, {}, {}, {}, {}, {}, {}
    for t in df.index:
        f = fund.get(t, {})
        if df.loc[t, "kind"] == "etf":
            q[t] = v[t] = ins[t] = rev[t] = np.nan; cat[t] = 0.0
        else:
            roe = _safe(f.get("returnOnEquity")); om = _safe(f.get("operatingMargins"))
            de = _safe(f.get("debtToEquity")); rg = _safe(f.get("revenueGrowth"))
            q[t] = np.nanmean([np.clip(roe, -0.5, 1.0) if not np.isnan(roe) else np.nan,
                               np.clip(om, -0.5, 0.6) if not np.isnan(om) else np.nan,
                               (-np.clip(de, 0, 400) / 400) if not np.isnan(de) else np.nan,
                               np.clip(rg, -0.5, 1.0) * 0.5 if not np.isnan(rg) else np.nan])
            fpe = _safe(f.get("forwardPE")); mc = _safe(f.get("marketCap")); fcf = _safe(f.get("freeCashflow"))
            ey = (1 / fpe) if fpe and fpe > 0 else np.nan
            fy = (fcf / mc) if mc and fcf and mc > 0 else np.nan
            v[t] = np.nanmean([np.clip(ey, -0.05, 0.25) if not np.isnan(ey) else np.nan,
                               np.clip(fy, -0.05, 0.25) if not np.isnan(fy) else np.nan])
            b, s = _safe(f.get("insider_buy_value"), 0.0), _safe(f.get("insider_sell_value"), 0.0)
            net = (b - s) / mc if mc else 0.0
            # signed log-scale: buys are rare and informative; sells are routine (comp) so half weight
            ins[t] = math.copysign(math.log1p(abs(net) * 1e4), net) * (1.0 if net > 0 else 0.5) + 0.25 * min(_safe(f.get("insider_buys_n"), 0), 4)
            rev[t] = _safe(f.get("rec_drift"), 0.0)
            d = _safe(f.get("days_to_earnings"))
            cat[t] = 1.0 if (not np.isnan(d) and 0 <= d <= 21) else 0.0
        ns = _safe(newsd.get(t, {}).get("sent_score"), 0.0)
        br = socd.get(t, {}).get("bull_ratio")
        sc = (br - 0.5) * 2 if br is not None else 0.0
        ctx[t] = float(np.clip(0.6 * ns + 0.4 * sc, -1, 1))
    df["z_quality"] = _z(pd.Series(q)); df["z_value"] = _z(pd.Series(v)); df["z_insider"] = _z(pd.Series(ins))
    df["z_revision"] = _z(pd.Series(rev)); df["catalyst"] = pd.Series(cat); df["context"] = pd.Series(ctx)
    for c in ("z_quality", "z_value", "z_insider", "z_revision"):
        df[c] = df[c].fillna(0.0)
    W = weights()   # learned weights if the loop has earned them, else the evidence priors
    comp = (W["momentum"] * df["z_momentum"] + W["trend"] * df["z_trend"]
            + W["quality"] * df["z_quality"] + W["value"] * df["z_value"]
            + W["lowvol"] * df["z_lowvol"] + W["insider"] * df["z_insider"]
            + W["revision"] * df["z_revision"] + W["catalyst"] * df["catalyst"]
            + W["context"] * df["context"])
    df["composite"] = comp
    # logistic → 0-100; slope 1.1 ⇒ composite 0→50, 0.5→63, 1.0→75, 2.0→90. The learning loop cools the
    # slope if realized outcomes show conviction is not calibrated.
    slope = float(learned().get("conviction_slope", 1.1))
    df["conviction"] = (100 / (1 + np.exp(-slope * comp))).round(0)
    return df.sort_values("composite", ascending=False)


def contributions(row: pd.Series) -> dict[str, float]:
    W = weights()
    m = {"momentum": row["z_momentum"], "trend": row["z_trend"], "quality": row.get("z_quality", 0),
         "value": row.get("z_value", 0), "lowvol": row["z_lowvol"], "insider": row.get("z_insider", 0),
         "revision": row.get("z_revision", 0), "catalyst": row.get("catalyst", 0), "context": row.get("context", 0)}
    return {k: round(float(W[k] * (0 if pd.isna(x) else x)), 3) for k, x in m.items()}
