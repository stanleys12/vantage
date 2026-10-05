#!/usr/bin/env python3
"""Offline unit tests (no network). Run: ./.venv/bin/python tests.py"""
import json, math, tempfile, traceback, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from vantage import news, factors, rank, edgar, reason, track, report
from vantage.config import WEIGHTS

RESULTS = []


def t(name):
    def deco(fn):
        try:
            fn(); RESULTS.append((name, True, ""))
        except Exception as e:
            RESULTS.append((name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"))
        return fn
    return deco


@t("news sentiment: positive / negative / neutral")
def _():
    assert news._sent("Nvidia beats estimates, raises guidance") == 1
    assert news._sent("Company misses on revenue, cuts outlook") == -1
    assert news._sent("Shares trade sideways ahead of Fed") == 0


@t("news dedupe collapses near-identical titles")
def _():
    items = [{"title": "Apple unveils new iPhone!", "age_h": 1}, {"title": "Apple unveils new iPhone", "age_h": 2},
             {"title": "Totally different story", "age_h": 3}]
    assert len(news._dedupe(items)) == 2


@t("z-score: mean≈0, clipped at ±3, constant series → 0")
def _():
    z = factors._z(pd.Series([1, 2, 3, 4, 100.0]))
    assert abs(z.mean()) < 0.5 and z.max() <= 3
    assert (factors._z(pd.Series([5.0] * 6)) == 0).all()


@t("stage1: rising name outranks falling name; trend flags correct")
def _():
    idx = pd.bdate_range(end="2026-09-26", periods=300)
    up = pd.Series(np.linspace(50, 100, 300), idx)
    down = pd.Series(np.linspace(100, 50, 300), idx)
    flat = pd.Series(75 + np.sin(np.arange(300) / 7), idx)
    px = pd.DataFrame({"UP": up, "DOWN": down, "FLAT": flat})
    s1 = factors.stage1(px, {"UP": "stock", "DOWN": "stock", "FLAT": "stock"})
    assert s1.index[0] == "UP" and s1.index[-1] == "DOWN"
    assert s1.loc["UP", "trend_raw"] == 3 and s1.loc["DOWN", "trend_raw"] == 0


@t("stage2 + rank.build: driver is distinctive exposure; scenarios ordered; P(beat) sane")
def _():
    idx = pd.bdate_range(end="2026-09-26", periods=300)
    rng = np.random.default_rng(0)
    px = pd.DataFrame({f"S{i}": 100 * np.cumprod(1 + rng.normal(0.0005 * (i % 3), 0.015, 300)) for i in range(12)}, idx)
    kinds = {c: "stock" for c in px.columns}
    s1 = factors.stage1(px, kinds)
    fund = {c: {"returnOnEquity": 0.3 if i == 0 else 0.05, "operatingMargins": 0.35 if i == 0 else 0.08, "debtToEquity": 20 if i == 0 else 150,
                "revenueGrowth": 0.2 if i == 0 else 0.02, "forwardPE": 15.0, "marketCap": 1e10, "freeCashflow": 5e8,
                "insider_buy_value": 5e6 if i == 1 else 0, "insider_sell_value": 0, "insider_buys_n": 3 if i == 1 else 0,
                "rec_drift": 0.0, "days_to_earnings": 10 if i == 2 else 60, "beta": 1.1} for i, c in enumerate(px.columns)}
    s2 = factors.stage2(s1, fund, {c: {"sent_score": 0} for c in px.columns}, {c: {} for c in px.columns})
    assert "composite" in s2 and s2["conviction"].between(0, 100).all()
    r0 = rank.build(s2.loc["S0"], 0.04, 1.1)
    assert r0["driver"] == "quality", r0["driver"]
    r1 = rank.build(s2.loc["S1"], 0.04, 1.1)
    assert r1["driver"] == "insider", r1["driver"]
    e = r0["expected_return_pct"]; assert e["bear"] < e["base"] < e["bull"]
    assert 0 <= r0["p_beat_spy"] <= 100 and abs(r0["expected_excess_annual_pct"]) < 10
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


@t("13F issuer→ticker matching: exact, suffix-stripped, ambiguous-safe, ETF-excluded, stale-skipped")
def _():
    gurus = {"X / Y": {"top": [{"issuer": "APPLE INC", "pct": 10.0}, {"issuer": "MICROSOFT CORP", "pct": 8.0},
                              {"issuer": "ALPHABET INC CL A", "pct": 5.0}, {"issuer": "GENERIC HOLDINGS", "pct": 1.0},
                              {"issuer": "SPDR S&P 500 ETF TR", "pct": 16.0}, {"issuer": "STATE STREET CORP", "pct": 1.0}],
                       "new": ["ALPHABET INC CL A"], "adds": [("APPLE INC", 25)], "stale": False},
             "Dead / Filer": {"top": [{"issuer": "APPLE INC", "pct": 50.0}], "new": [], "adds": [], "stale": True}}
    names = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "GOOGL": "Alphabet Inc. (Class A)",
             "GOOG": "Alphabet Inc. (Class C)", "STT": "State Street Corporation"}
    h = edgar.match_guru_holdings(gurus, names)
    assert "AAPL" in h and "+25%" in h["AAPL"][0] and len(h["AAPL"]) == 1   # stale filer skipped
    assert "MSFT" in h
    assert any("NEW" in x for x in h.get("GOOGL", []) + h.get("GOOG", []))
    assert h["STT"] == ["X (1.0%)"]   # only the real State Street position, NOT the SPDR ETF (16%)


@t("reason: JSON extraction from fence, from trailing object, and garbage→None; adjust is clamped")
def _():
    assert reason._extract_json('blah ```json\n{"picks": {"A": {"adjust": 3}}}\n```')["picks"]["A"]["adjust"] == 3
    assert reason._extract_json('preamble {"a": {"b": [1,2]}, "picks": {}} trailing')["a"]["b"] == [1, 2]
    assert reason._extract_json("no json here") is None
    cands = [{"ticker": "T", "conviction": 80, "rank_info": {"driver": "momentum", "horizon": "3-9 months",
              "contributions": {"momentum": 0.4, "quality": 0.1}}, "risk_flags": []}]
    out = reason.reason(cands, {"_regime": {}}, [], {}, use_llm=False)
    assert out["source"] == "rule-based" and out["picks"]["T"]["verdict"] == "Buy"


@t("track: record dedupes same-day ticker; verdict thresholds")
def _():
    tmp = Path(tempfile.mkdtemp()); orig = track.LOG; track.LOG = tmp / "p.jsonl"
    try:
        p = [{"ticker": "AAA", "rank": 1, "conviction": 70, "price": 10.0, "driver": "momentum", "p_beat_spy": 55}]
        track.record(p, 500.0, "2026-09-28"); track.record(p, 500.0, "2026-09-28")
        assert len(track.LOG.read_text().splitlines()) == 1
    finally:
        track.LOG = orig
    assert "Too early" in track._verdict({})
    assert "noise" in track._verdict({"1m": {"n": 10, "clusters": 3, "t_cluster": 3}})          # too few dates
    assert "UNDERPERFORMING" in track._verdict({"1m": {"n": 240, "clusters": 25, "t_cluster": -2.5, "median_excess_pct": -1, "hit_rate_pct": 40}})
    assert "survives" in track._verdict({"1m": {"n": 240, "clusters": 25, "t_cluster": 2.5, "median_excess_pct": 0.3, "hit_rate_pct": 54, "mean_excess_ex_top5pct": 0.2}})
    assert "No statistically" in track._verdict({"1m": {"n": 240, "clusters": 25, "t_cluster": 0.4, "median_excess_pct": 0.1, "hit_rate_pct": 51}})


@t("track.evaluate: pick dated beyond the last price bar (pre-market run) does not crash")
def _():
    import json as _j
    tmp = Path(tempfile.mkdtemp()); oL, oC, oO, oP = track.LOG, track.CARD, track.OUTCOMES, track.D.prices
    try:
        track.LOG, track.CARD, track.OUTCOMES = tmp / "p.jsonl", tmp / "c.json", tmp / "o.json"
        idx = pd.bdate_range("2026-08-01", "2026-09-28")            # last bar = 09-28
        px = pd.DataFrame({"AAA": np.linspace(10, 12, len(idx)), "SPY": np.linspace(500, 510, len(idx))}, idx)
        track.D.prices = lambda tickers, period="1y": px
        rows = [{"date": "2026-08-15", "ticker": "AAA", "rank": 1, "conviction": 70, "price": 10.0, "spy": 500.0, "driver": "momentum", "p_beat_spy": 55},
                {"date": "2026-09-28", "ticker": "AAA", "rank": 1, "conviction": 70, "price": 12.0, "spy": 510.0, "driver": "momentum", "p_beat_spy": 55},
                {"date": "2026-09-29", "ticker": "AAA", "rank": 1, "conviction": 70, "price": 12.0, "spy": 510.0, "driver": "momentum", "p_beat_spy": 55}]  # beyond last bar
        track.LOG.write_text("\n".join(_j.dumps(r) for r in rows) + "\n")
        card = track.evaluate()                                        # must not raise IndexError
        assert card["n_picks"] == 3 and "1w" in card["by_horizon"] and card["by_horizon"]["1w"]["n"] == 1
    finally:
        track.LOG, track.CARD, track.OUTCOMES, track.D.prices = oL, oC, oO, oP


@t("track.joined: 1m outcomes of old picks survive past the scorecard's 200-row detail cap")
def _():
    tmp = Path(tempfile.mkdtemp()); oL, oC, oO, oP = track.LOG, track.CARD, track.OUTCOMES, track.D.prices
    try:
        track.LOG, track.CARD, track.OUTCOMES = tmp / "p.jsonl", tmp / "c.json", tmp / "o.json"
        idx = pd.bdate_range("2026-06-01", "2026-09-28")
        names = [f"T{i}" for i in range(18)]
        px = pd.DataFrame({**{n: np.linspace(10, 12, len(idx)) for n in names}, "SPY": np.linspace(500, 510, len(idx))}, idx)
        track.D.prices = lambda tickers, period="1y": px
        rows = [{"date": d.date().isoformat(), "ticker": n, "rank": i + 1, "conviction": 70, "price": 10.0, "spy": 500.0,
                 "driver": "momentum", "p_beat_spy": 55} for d in idx[:-1] for i, n in enumerate(names)]
        track.LOG.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        card = track.evaluate()
        assert len(card["detail"]) == 200
        j = track.joined()
        assert len(j) == len(rows)
        assert sum("1m" in r for r in j) == card["by_horizon"]["1m"]["n"] > 1000
    finally:
        track.LOG, track.CARD, track.OUTCOMES, track.D.prices = oL, oC, oO, oP


@t("cluster t: one observation per date; tail share flags outlier-driven means")
def _():
    pairs = [(f"d{i}", v) for i in range(6) for v in (2.0 + i * 0.1, 1.0 + i * 0.1)]
    c = track.cluster_t(pairs); assert c["clusters"] == 6 and c["t_cluster"] and c["t_cluster"] > 5
    assert track.cluster_t([("d1", 1.0), ("d2", 1.0)])["t_cluster"] is None
    ts = track.tail_share([50.0] + [-0.5] * 39)
    assert ts["top5pct_share_of_total"] > 0.99 and ts["mean_excess_ex_top5pct"] < 0
    s = {"clusters": 25, "t_cluster": 3.0, "median_excess_pct": -0.5, "hit_rate_pct": 48, "mean_excess_ex_top5pct": -0.2}
    assert "few big winners" in track.judge(s)
    s2 = {**s, "median_excess_pct": 0.4, "hit_rate_pct": 55, "mean_excess_ex_top5pct": 0.3}
    assert "survives" in track.judge(s2)
    assert "independent pick-dates" in track.judge({"clusters": 5, "t_cluster": 9})


@t("learn.adapt: gated below minimum; shrinkage bounded; refuses fat-tail; weights sum to 1")
def _():
    from vantage import learn
    small = {"n_scored": 40, "n_dates": 4, "factor_efficacy": {k: 1.0 for k in learn.EVIDENCE}}
    out = learn.adapt(small); assert out["lambda"] == 0 and out["weights"] == WEIGHTS and "priors unchanged" in out["note"]
    big = {"n_scored": 240, "n_dates": 30, "overall": {"mean_excess_pct": 1.0, "mean_excess_ex_top5pct": 0.5},
           "factor_efficacy": {"momentum": 0.0, "trend": 0.0, "quality": 3.0, "value": 0.0, "lowvol": 0.0, "insider": 0.0, "revision": 0.0}}
    out = learn.adapt(big)
    w = out["weights"]; assert abs(sum(w.values()) - 1) < 1e-6 and 0 < out["lambda"] <= learn.MAX_LAMBDA
    assert w["quality"] > WEIGHTS["quality"] and w["momentum"] < WEIGHTS["momentum"]
    for k in learn.EVIDENCE:   # bounded: within [25%, 200%] of prior (before renormalization slack)
        assert w[k] >= learn.FLOOR * WEIGHTS[k] * 0.9 and w[k] <= learn.CEIL * WEIGHTS[k] * 1.1
    fat = {**big, "overall": {"mean_excess_pct": 2.0, "mean_excess_ex_top5pct": -0.3}}
    out = learn.adapt(fat); assert out["lambda"] == 0 and "fat tail" in out["note"]


@t("weekly.select: PEAD beat ranks first, last-week spike penalized, event bucket capped, ETFs excluded")
def _():
    from vantage import weekly
    def mk(t, **kw):
        base = {"ticker": t, "name": t, "sector": "x", "kind": "stock", "price": 100.0, "conviction": 70, "final_conviction": 70,
                "ret_1w": 2.0, "ret_1m": 5.0, "ret_3m": 8.0, "pct_52w": 95.0, "z": {"momentum": 1.0, "trend": 1.0, "quality": 0.5, "value": 0.0,
                "lowvol": 0.0, "insider": 0.0, "revision": 0.0, "catalyst": 0.0, "context": 0.0}, "news": {"sent_score": 0.0, "fresh_24h": 0, "items": [], "n": 0},
                "fundamentals": {}, "reasoning": {}, "risk_flags": [], "gurus": [], "social": {}, "intraday": {},
                "rank_info": {"annual_vol_pct": 40.0, "beta": 1.1, "expected_excess_annual_pct": 3.0, "contributions": {}}}
        base.update(kw); return base
    cl = [mk("PEAD", fundamentals={"days_since_earnings": 4, "eps_surprise_pct": 20.0}),
          mk("SPIKE", ret_1w=18.0),
          mk("EV1", fundamentals={"days_to_earnings": 2}), mk("EV2", fundamentals={"days_to_earnings": 3}), mk("EV3", fundamentals={"days_to_earnings": 4}),
          mk("PLAIN"), mk("ETF", kind="etf"), mk("LOTTO", rank_info={"annual_vol_pct": 300.0, "beta": 1.5, "expected_excess_annual_pct": 3.0, "contributions": {}})]
    out, excluded = weekly.select(cl, 0.04, n=6, max_event=2)
    ticks = [w["ticker"] for w in out]
    assert ticks[0] == "PEAD" and out[0]["bucket"] == "drift"
    assert "ETF" not in ticks and "LOTTO" not in ticks and any(x["ticker"] == "LOTTO" for x in excluded)
    assert sum(1 for w in out if w["bucket"] == "event") <= 2
    spike = [w for w in out if w["ticker"] == "SPIKE"]
    assert not spike or spike[0]["weekly_score"] < out[0]["weekly_score"] - 0.3
    e = out[0]["rank_info"]["expected_return_pct"]; assert e["bear"] < e["base"] < e["bull"] and 45 <= out[0]["rank_info"]["p_beat_spy"] <= 60


class FakeBroker:
    """Fills every order immediately at the day's price; no margin logic of its own."""
    def __init__(self, cash=100000.0, positions=None):
        self.cash, self.pos, self.orders, self.today, self.drift = cash, dict(positions or {}), {}, None, {}
    def price(self, sym):
        import datetime as _d
        n = (_d.date.fromisoformat(self.today) - _d.date(2026, 10, 5)).days
        return round(100.0 * (1 + self.drift.get(sym, 0.0)) ** n, 4)
    def account(self):
        return {"cash": str(self.cash), "equity": str(self.cash + sum(q * self.price(s_) for s_, q in self.pos.items()))}
    def positions(self):
        return [{"symbol": s_, "current_price": str(self.price(s_)), "qty": str(q)} for s_, q in self.pos.items() if q > 1e-9]
    def is_trading_day(self, d):
        import datetime as _d
        return _d.date.fromisoformat(d).weekday() < 5
    def sessions_since(self, opened, today):
        return int(np.busday_count(opened, today))
    def submit(self, symbol, side, coid, notional=None, qty=None):
        px = self.price(symbol); q = (notional / px) if notional is not None else qty
        if side == "buy": self.cash -= q * px; self.pos[symbol] = self.pos.get(symbol, 0) + q
        else: self.cash += q * px; self.pos[symbol] = self.pos.get(symbol, 0) - q
        self.orders[coid] = {"status": "filled", "filled_qty": str(q), "filled_avg_price": str(px)}
        return self.orders[coid]
    def order(self, coid):
        return self.orders.get(coid)


@t("paper: start, idempotent rerun, 5-day weekly exit, weekly core rebalance, no margin, refuses dirty account")
def _():
    from vantage import paper
    tmp = Path(tempfile.mkdtemp()) / "ledger.json"
    br = FakeBroker(); br.drift = {"W1": 0.01, "C1": 0.005, "SPY": 0.001}
    core = lambda names: [{"ticker": n, "rank": i + 1, "verdict": "Buy" if i < 6 else "Watch", "price": 100.0} for i, n in enumerate(names)]
    wk = [{"ticker": "W1", "price": 100.0}, {"ticker": "W2", "price": 100.0}]
    days = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
    week1 = core(["C1", "C2", "C3", "C4", "C5", "C6", "C7"]); week2 = core(["C1", "C2", "C3", "C4", "C9", "C6", "C7"])
    br.today = days[0]
    assert paper.step(week1, wk, days[0], trade=False, broker=br, ledger_path=tmp)["enabled"] is False     # refresh never starts the book
    out = paper.step(week1, wk, days[0], broker=br, ledger_path=tmp)
    L = json.loads(tmp.read_text())
    assert out["enabled"] and L["start_equity"] == 100000.0
    assert sorted(l["ticker"] for l in L["lots"] if l["sleeve"] == "core") == ["C1", "C2", "C3", "C4", "C5"]
    assert sum(l["notional"] for l in L["lots"]) == 30000 + 2 * 1000 + 5 * 8000
    n0 = len(L["lots"]); paper.step(week1, wk, days[0], broker=br, ledger_path=tmp)                           # same-day rerun
    assert len(json.loads(tmp.read_text())["lots"]) == n0
    for d in days[1:5]:
        br.today = d; paper.step(week1, wk, d, broker=br, ledger_path=tmp); assert br.cash >= -1e-6
    L = json.loads(tmp.read_text())
    assert not any(l["status"] in ("closing", "closed") for l in L["lots"])                                   # nothing 5 sessions old yet
    assert len([l for l in L["lots"] if l["sleeve"] == "core"]) == 5 and len([l for l in L["lots"] if l["sleeve"] == "spy"]) == 1
    br.today = days[5]; paper.step(week2, wk, days[5], broker=br, ledger_path=tmp)                           # Monday of week 2
    L = json.loads(tmp.read_text())
    closing = {(l["sleeve"], l["ticker"], l["opened"]) for l in L["lots"] if l["status"] == "closing"}
    assert ("weekly", "W1", days[0]) in closing and ("weekly", "W2", days[0]) in closing                      # 5 sessions old → sold
    assert ("core", "C5", days[0]) in closing                                                                 # left the top five
    assert any(l["sleeve"] == "core" and l["ticker"] == "C9" and l["opened"] == days[5] for l in L["lots"])   # replacement bought
    assert not any(l["sleeve"] == "weekly" and l["opened"] == days[1] and l["status"] != "open" for l in L["lots"])
    for d in days[6:]:
        br.today = d; out = paper.step(week2, wk, d, broker=br, ledger_path=tmp); assert br.cash >= -1e-6
    w = out["sleeves"]["weekly"]
    assert w["closed_lots"] >= 4 and w["realized"] > 0 and out["sleeves"]["spy"]["open_lots"] == 1 and out["spy_return_pct"] > 0
    assert out["sleeves"]["core"]["closed_lots"] == 1 and len(out["equity_curve"]) == len(days)
    assert abs(out["total_pnl"] - sum(v["pnl"] for v in out["sleeves"].values())) < 1.0                       # sleeves add up to the account
    # no margin: a broke account skips buys instead of borrowing
    tmp2 = Path(tempfile.mkdtemp()) / "l.json"; poor = FakeBroker(cash=100000.0); poor.today = days[0]
    paper.step(week1, wk, days[0], broker=poor, ledger_path=tmp2); poor.cash = 500.0; poor.today = days[1]
    paper.step(week1, wk, days[1], broker=poor, ledger_path=tmp2)
    assert any("SKIP buy" in x and "no margin" in x for x in json.loads(tmp2.read_text())["log"]) and poor.cash >= 0
    # a paper account that already holds positions is refused
    dirty = FakeBroker(positions={"XYZ": 10}); dirty.today = days[0]
    r = paper.step(week1, wk, days[0], broker=dirty, ledger_path=Path(tempfile.mkdtemp()) / "l.json")
    assert r["enabled"] is False and "Refusing to start" in r["note"]
    assert paper.PAPER_BASE.startswith("https://paper-api.")


@t("report.write produces md + json for a minimal run")
def _():
    from vantage import config
    tmp = Path(tempfile.mkdtemp()); orig = config.REPORTS; report.REPORTS = tmp
    try:
        run = {"date": "2000-01-01", "generated_at": "x", "n_universe": 1, "n_deep": 1,
               "macro": {"_regime": {"volatility": "calm", "trend": "risk-on"}, "S&P 500": {"level": 1, "1d": 0, "1w": 0, "1m": 0, "3m": 0}},
               "reasoning": {"market_view": "v"}, "scorecard": {}, "market_news": [], "gurus": {},
               "picks": [{"rank": 1, "ticker": "T", "name": "Test", "sector": "Tech", "kind": "stock", "price": 1.0, "conviction": 70,
                          "final_conviction": 72, "ret_1m": 1, "ret_3m": 2, "ret_6m": 3, "pct_52w": 90, "news": {"n": 0, "fresh_24h": 0, "sent_score": 0, "items": []},
                          "fundamentals": {}, "reasoning": {"verdict": "Buy", "adjust": 2, "thesis": "t"}, "risk_flags": [], "gurus": [], "social": {},
                          "rank_info": {"driver": "momentum", "horizon": "3-9 months", "expected_excess_annual_pct": 2.0, "p_beat_spy": 55, "beta": 1.0, "annual_vol_pct": 30.0,
                                        "expected_return_pct": {"bear": -10, "base": 5, "bull": 20}, "contributions": {"momentum": 0.3}}}]}
        md, js = report.write(run)
        assert Path(md).exists() and Path(js).exists() and "Ranked picks" in Path(md).read_text()
    finally:
        report.REPORTS = orig


if __name__ == "__main__":
    ok = sum(1 for _, p, _ in RESULTS if p)
    for name, p, err in RESULTS:
        print(("PASS " if p else "FAIL ") + name + ("" if p else "\n     " + err.strip().replace("\n", "\n     ")))
    print(f"\n{ok}/{len(RESULTS)} passed")
    raise SystemExit(0 if ok == len(RESULTS) else 1)
