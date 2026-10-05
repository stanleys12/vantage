"""Vantage configuration: paths, universe, factor weights, gurus, honesty constants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
CACHE = DATA / "cache"
for d in (DATA, REPORTS, CACHE):
    d.mkdir(parents=True, exist_ok=True)

# SEC's Archives host requires a "Name contact@domain" User-Agent (format only; it is not
# verified). Replace the placeholder with your real name/email to be a good citizen.
USER_AGENT_CONTACT = "Vantage Research vantage-research@example.com"
UA = {"User-Agent": USER_AGENT_CONTACT}

# ---- universe ---------------------------------------------------------------
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
ETFS = {
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "DIA": "Dow 30", "VTI": "Total US market",
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health care", "XLI": "Industrials",
    "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real estate", "XLC": "Communication", "SMH": "Semiconductors", "XBI": "Biotech", "KRE": "Regional banks",
    "XOP": "Oil & gas E&P", "GLD": "Gold", "SLV": "Silver", "TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries",
    "HYG": "High-yield credit", "LQD": "IG credit", "EEM": "Emerging markets", "EFA": "Developed ex-US",
    "VNQ": "REITs", "ARKK": "ARK innovation", "USO": "Crude oil", "IBIT": "Bitcoin",
}
BENCHMARK = "SPY"

# Funnel: stage-1 keeps DEEP_DIVE_N by balanced score + a low-vol sleeve (diversity), deep-dives all of
# them (cheap), then the LLM reasons over the top LLM_N shortlist; FINAL_N make the brief.
DEEP_DIVE_N = 50
LOWVOL_SLEEVE = 15
LLM_N = 25
FINAL_N = 12

# ---- factor model ----------------------------------------------------------
# Only inputs with long-run academic evidence carry real weight. News/social are
# context (small weight, and capped) so the desk is informed by them, not swayed.
WEIGHTS = {
    "momentum": 0.28,   # 12-1 month return (Jegadeesh-Titman); strongest documented anomaly
    "trend":    0.10,   # price vs 50/200d MAs (time-series momentum)
    "quality":  0.20,   # ROE, margins, low leverage (Asness "Quality Minus Junk")
    "value":    0.12,   # earnings/FCF yield (Fama-French HML; weaker recently, still real)
    "lowvol":   0.08,   # low realized vol (low-vol anomaly)
    "insider":  0.10,   # net open-market insider buying (Lakonishok-Lee: real, modest)
    "revision": 0.05,   # analyst recommendation drift
    "catalyst": 0.04,   # upcoming earnings / event window
    "context":  0.03,   # news + social sentiment, CAPPED: informs, never decides
}

# ---- gurus (SEC CIKs). 13F = quarter-end positions filed up to 45 days later. ----
GURUS = {
    "Bill Ackman / Pershing Square":      "0001336528",
    "Warren Buffett / Berkshire":         "0001067983",
    "Michael Burry / Scion":              "0001649339",
    "David Tepper / Appaloosa":           "0001656456",
    "Stanley Druckenmiller / Duquesne":   "0001536411",
    "Seth Klarman / Baupost":             "0001061768",
    "Dan Loeb / Third Point":             "0001040273",
    "David Einhorn / Greenlight":         "0001079114",
    "Ray Dalio / Bridgewater":            "0001350694",
}

# Investor lenses for the reasoning layer (persona reasoning, NOT their real positions).
LENSES = ["Buffett (moat, durable earnings, price vs value)",
          "Ackman (quality franchise + activist catalyst)",
          "Burry (contrarian, balance-sheet, what the crowd is wrong about)",
          "Druckenmiller (macro regime + liquidity, concentrate when right)",
          "Damodaran (valuation discipline: narrative must tie to numbers)"]

# Holding horizons by dominant driver (evidence-based, not guesses).
HORIZON = {"momentum": "3-9 months", "trend": "3-6 months", "quality": "1-3 years",
           "value": "1-3 years", "lowvol": "6-18 months", "insider": "6-12 months",
           "catalyst": "through the event (weeks)", "revision": "1-3 months", "context": "n/a"}

# Historical long-run factor premia (annualized, gross, rough consensus). Used ONLY to
# derive an honest, modest expected-excess-return; never to promise returns.
FACTOR_PREMIUM = {"momentum": 0.06, "quality": 0.04, "value": 0.03, "lowvol": 0.02,
                  "insider": 0.04, "trend": 0.03, "revision": 0.02, "catalyst": 0.0, "context": 0.0}
EQUITY_RISK_PREMIUM = 0.05   # over risk-free, long-run

# ---- learning (see learn.py). Priors above; learned overrides live in data/learned.json ------
import json as _json
LEARNED = DATA / "learned.json"


def learned() -> dict:
    """Learned parameters, or {} if none / invalid. Keys: weights, max_adj, conviction_slope, n, lambda."""
    try:
        if LEARNED.exists():
            j = _json.loads(LEARNED.read_text())
            w = j.get("weights")
            if w and set(w) == set(WEIGHTS) and abs(sum(w.values()) - 1) < 1e-6:
                return j
    except Exception:
        pass
    return {}


def weights() -> dict:
    """Effective factor weights: learned if present and valid, else the evidence priors."""
    return dict(learned().get("weights") or WEIGHTS)


DISCLAIMER = ("Vantage is a research desk, not a crystal ball. No system reliably predicts "
              "returns; these are evidence-weighted tilts with honest scenario ranges, and the "
              "scorecard tracks every pick against SPY so you can see whether it adds value. "
              "Not financial advice.")
