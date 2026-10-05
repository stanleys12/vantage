"""Paper portfolio: executes the desk's picks in a DEDICATED Alpaca paper account under fixed rules,
so gains and losses are verified by real order records rather than by closing-price arithmetic.

RULES v1 (frozen 2026-10-02 — change them only by bumping RULES_VERSION and restarting the ledger):
  capital   start equity split 40% core / 30% weekly sleeve / 30% SPY benchmark. No margin, ever.
  SPY       bought once on day one, held. It is the yardstick the other two sleeves are judged against.
  core      first recorded run of each ISO week: target = top five Buy-rated picks by rank (filled from
            the next-ranked non-Avoid names if fewer than five Buys). Sell holdings that left the target,
            buy the new names at 8% of start equity each. Names that stay are left untouched.
  weekly    every recorded run: buy each weekly pick at 1% of start equity; sell every weekly lot once it
            is five trading days old, no matter what.
  orders    market, day. A buy is skipped (and logged) if cash would go negative.

Paper fills are optimistic (quoted price, no market impact) and this adds no statistical evidence beyond the
scorecard — it is the same picks on the same days. What it adds is sizing, turnover, cash and real fills.
Keys live in ~/vantage/.env as VANTAGE_ALPACA_KEY / VANTAGE_ALPACA_SECRET. The endpoint is hard-wired to
Alpaca's PAPER host; this module cannot reach a live account."""
from __future__ import annotations
import json, math, os, time
from datetime import date, datetime
from pathlib import Path
import requests
from .config import ROOT, DATA

RULES_VERSION = 1
ALLOC = {"core": 0.40, "weekly": 0.30, "spy": 0.30}
CORE_N, CORE_LOT, WEEKLY_LOT, WEEKLY_HOLD_DAYS = 5, 0.08, 0.01, 5
LEDGER = DATA / "paper_ledger.json"
ENV = ROOT / ".env"
PAPER_BASE = "https://paper-api.alpaca.markets"     # hard-wired: paper only


def _keys() -> tuple[str | None, str | None]:
    k, s = os.environ.get("VANTAGE_ALPACA_KEY"), os.environ.get("VANTAGE_ALPACA_SECRET")
    if (not k or not s) and ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                a, b = line.split("=", 1); a, b = a.strip(), b.strip().strip('"').strip("'")
                if a == "VANTAGE_ALPACA_KEY": k = b
                if a == "VANTAGE_ALPACA_SECRET": s = b
    return (k or None), (s or None)


class Broker:
    """Thin Alpaca paper client. Every method talks to PAPER_BASE only."""
    def __init__(self, key: str, secret: str):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def _req(self, method: str, path: str, **kw):
        r = requests.request(method, PAPER_BASE + path, headers=self.h, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"alpaca {method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else {}

    def account(self) -> dict: return self._req("GET", "/v2/account")
    def positions(self) -> list[dict]: return self._req("GET", "/v2/positions")

    def is_trading_day(self, d: str) -> bool:
        return any(c["date"] == d for c in self._req("GET", "/v2/calendar", params={"start": d, "end": d}))

    def sessions_since(self, opened: str, today: str) -> int:
        """Trading sessions strictly after `opened`, up to and including `today`."""
        cal = self._req("GET", "/v2/calendar", params={"start": opened, "end": today})
        return sum(1 for c in cal if opened < c["date"] <= today)

    def submit(self, symbol: str, side: str, coid: str, notional: float | None = None, qty: float | None = None) -> dict:
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day", "client_order_id": coid}
        if notional is not None: body["notional"] = f"{notional:.2f}"
        else: body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
        return self._req("POST", "/v2/orders", json=body)

    def order(self, coid: str) -> dict | None:
        try: return self._req("GET", "/v2/orders:by_client_order_id", params={"client_order_id": coid})
        except RuntimeError: return None


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"rules_version": RULES_VERSION, "started": None, "start_equity": None, "last_trade_date": None,
            "last_core_week": None, "lots": [], "log": [], "equity_curve": []}


def _log(L: dict, today: str, msg: str):
    L["log"].append(f"{today} {msg}"); L["log"] = L["log"][-300:]


def _reconcile(L: dict, br, today: str):
    for lot in L["lots"]:
        if lot["status"] == "pending":
            o = br.order(lot["buy_coid"])
            if not o: continue
            if o["status"] == "filled":
                lot.update(status="open", qty=float(o["filled_qty"]), entry_price=float(o["filled_avg_price"]),
                           cost=float(o["filled_qty"]) * float(o["filled_avg_price"]))
                _log(L, today, f"FILLED buy {lot['ticker']} [{lot['sleeve']}] {lot['qty']:.4f} @ {lot['entry_price']:.2f}")
            elif o["status"] in ("canceled", "expired", "rejected", "done_for_day"):
                lot["status"] = "failed"; _log(L, today, f"buy {lot['ticker']} [{lot['sleeve']}] did not fill ({o['status']})")
        elif lot["status"] == "closing":
            o = br.order(lot["sell_coid"])
            if not o: continue
            if o["status"] == "filled":
                px = float(o["filled_avg_price"])
                lot.update(status="closed", exit_price=px, closed=today, realized=(px - lot["entry_price"]) * lot["qty"])
                _log(L, today, f"FILLED sell {lot['ticker']} [{lot['sleeve']}] @ {px:.2f}  P&L ${lot['realized']:+.2f}")
            elif o["status"] in ("canceled", "expired", "rejected", "done_for_day"):
                lot["status"] = "open"; lot["sell_coid"] = None; _log(L, today, f"sell {lot['ticker']} did not fill ({o['status']}) — will retry")


def _buy(L, br, today, sleeve, ticker, notional, ref_price, cash) -> float:
    """Submit one buy. Returns the cash it reserves (0 if skipped)."""
    if notional > cash:
        _log(L, today, f"SKIP buy {ticker} [{sleeve}] ${notional:,.0f}: only ${cash:,.0f} cash (no margin)"); return 0.0
    coid = f"vz-{sleeve}-{ticker}-{today}-b".replace(".", "_")
    try:
        try:
            br.submit(ticker, "buy", coid, notional=notional)
        except RuntimeError as e:                       # fractional/notional refused → whole shares
            q = math.floor(notional / ref_price) if ref_price else 0
            if q < 1:
                _log(L, today, f"SKIP buy {ticker} [{sleeve}]: notional refused and <1 whole share ({str(e)[:80]})"); return 0.0
            br.submit(ticker, "buy", coid + "q", qty=q); coid += "q"; notional = q * ref_price
    except RuntimeError as e:
        _log(L, today, f"buy {ticker} [{sleeve}] REJECTED: {str(e)[:120]}"); return 0.0
    L["lots"].append({"id": coid, "sleeve": sleeve, "ticker": ticker, "opened": today, "buy_coid": coid, "notional": round(notional, 2),
                      "status": "pending", "qty": None, "entry_price": None, "cost": None, "sell_coid": None, "exit_price": None,
                      "closed": None, "realized": None})
    _log(L, today, f"BUY  {ticker} [{sleeve}] ${notional:,.0f}")
    return notional


def _sell(L, br, today, lot, why):
    coid = f"{lot['id']}-s-{today}"
    try:
        br.submit(lot["ticker"], "sell", coid, qty=lot["qty"])
        lot.update(status="closing", sell_coid=coid); _log(L, today, f"SELL {lot['ticker']} [{lot['sleeve']}] ({why})")
    except RuntimeError as e:
        _log(L, today, f"sell {lot['ticker']} REJECTED: {str(e)[:120]}")


def _summary(L: dict, br, today: str, note: str | None = None) -> dict:
    px = {p["symbol"]: float(p["current_price"]) for p in br.positions()}
    acct = br.account(); start = L["start_equity"] or float(acct["equity"])
    sleeves = {}
    for s, w in ALLOC.items():
        lots = [l for l in L["lots"] if l["sleeve"] == s]
        op = [l for l in lots if l["status"] in ("open", "closing")]
        realized = sum(l["realized"] or 0 for l in lots if l["status"] == "closed")
        mv = sum(l["qty"] * px.get(l["ticker"], l["entry_price"]) for l in op)
        cost = sum(l["cost"] for l in op)
        closed = [l for l in lots if l["status"] == "closed"]
        pnl = realized + (mv - cost)
        sleeves[s] = {"allocated": round(start * w, 2), "deployed": round(cost, 2), "market_value": round(mv, 2),
                      "realized": round(realized, 2), "unrealized": round(mv - cost, 2), "pnl": round(pnl, 2),
                      "return_on_allocated_pct": round(pnl / (start * w) * 100, 2) if start else None,
                      "open_lots": len(op), "closed_lots": len(closed), "pending": sum(1 for l in lots if l["status"] == "pending"),
                      "win_rate_pct": round(sum(1 for l in closed if (l["realized"] or 0) > 0) / len(closed) * 100) if closed else None}
    spy = sleeves["spy"]; spy_ret = (spy["pnl"] / spy["deployed"] * 100) if spy["deployed"] else None
    for s in ("core", "weekly"):
        d = sleeves[s]
        # return on capital actually put to work, the fair comparison with SPY while the sleeve is still ramping up
        turnover = sum(l["cost"] or 0 for l in L["lots"] if l["sleeve"] == s and l["status"] in ("open", "closing", "closed"))
        d["return_on_deployed_pct"] = round(d["pnl"] / turnover * 100, 2) if turnover else None
    out = {"enabled": True, "rules_version": L["rules_version"], "started": L["started"], "asof": today,
           "start_equity": start, "equity": round(float(acct["equity"]), 2), "cash": round(float(acct["cash"]), 2),
           "total_pnl": round(float(acct["equity"]) - start, 2), "total_return_pct": round((float(acct["equity"]) / start - 1) * 100, 2) if start else None,
           "spy_return_pct": round(spy_ret, 2) if spy_ret is not None else None, "sleeves": sleeves,
           "open_positions": [{"ticker": l["ticker"], "sleeve": l["sleeve"], "opened": l["opened"], "qty": round(l["qty"], 4),
                               "entry": l["entry_price"], "last": px.get(l["ticker"]),
                               "pnl": round((px.get(l["ticker"], l["entry_price"]) - l["entry_price"]) * l["qty"], 2)}
                              for l in L["lots"] if l["status"] in ("open", "closing")],
           "recent": L["log"][-14:], "note": note,
           "caveat": "Paper fills are optimistic and this is the same picks as the scorecard — it verifies dollars, sizing and turnover, not statistical edge."}
    curve = L["equity_curve"]
    row = {"date": today, "equity": out["equity"], **{s: sleeves[s]["pnl"] for s in ALLOC}}
    if curve and curve[-1]["date"] == today: curve[-1] = row
    else: curve.append(row)
    out["equity_curve"] = curve[-260:]
    return out


def step(core_picks: list[dict], weekly_picks: list[dict], today: str | None = None, trade: bool = True,
         broker=None, ledger_path: Path | None = None) -> dict:
    """One daily pass. trade=False only reconciles fills and marks to market (used by the afternoon refresh)."""
    today = today or date.today().isoformat(); path = ledger_path or LEDGER
    if broker is None:
        k, s = _keys()
        if not k or not s:
            return {"enabled": False, "note": "No paper account connected. Create a NEW Alpaca paper account and put its keys in "
                    "~/vantage/.env as VANTAGE_ALPACA_KEY=… and VANTAGE_ALPACA_SECRET=… — the next morning run starts the portfolio."}
        broker = Broker(k, s)
    try:
        L = _load(path)
        acct = broker.account()
        if L["started"] is None:
            if not trade:
                return {"enabled": False, "note": "Paper account connected; the portfolio starts on the next recorded morning run."}
            pos = broker.positions()
            if pos:
                return {"enabled": False, "note": f"Refusing to start: this paper account already holds {len(pos)} positions. "
                        "Vantage needs its own empty paper account so its P&L is not mixed with anything else."}
            L.update(started=today, start_equity=float(acct["equity"]))
            _log(L, today, f"START rules v{RULES_VERSION}: ${L['start_equity']:,.0f} → core 40% / weekly 30% / SPY 30%, no margin")
        _reconcile(L, broker, today)
        note = None
        if trade and L["last_trade_date"] != today:
            if not broker.is_trading_day(today):
                note = f"{today} is not a trading day — no orders."
            else:
                start = L["start_equity"]; cash = float(acct["cash"])
                live = lambda sl: [l for l in L["lots"] if l["sleeve"] == sl and l["status"] in ("pending", "open", "closing")]
                if not live("spy") and not any(l["sleeve"] == "spy" for l in L["lots"]):
                    cash -= _buy(L, broker, today, "spy", "SPY", start * ALLOC["spy"], None, cash)
                for lot in [l for l in L["lots"] if l["sleeve"] == "weekly" and l["status"] == "open"]:
                    if broker.sessions_since(lot["opened"], today) >= WEEKLY_HOLD_DAYS:
                        _sell(L, broker, today, lot, f"{WEEKLY_HOLD_DAYS} trading days elapsed")
                held_w = {l["ticker"] for l in live("weekly") if l["opened"] == today}
                for w in weekly_picks:
                    if w["ticker"] not in held_w:
                        cash -= _buy(L, broker, today, "weekly", w["ticker"], start * WEEKLY_LOT, w.get("price"), cash)
                wk = "{}-W{:02d}".format(*datetime.fromisoformat(today).isocalendar()[:2])
                if L["last_core_week"] != wk:
                    ranked = sorted(core_picks, key=lambda p: p["rank"])
                    buys = [p for p in ranked if p.get("verdict") == "Buy"]
                    target = (buys + [p for p in ranked if p.get("verdict") not in ("Buy", "Avoid")])[:CORE_N]
                    tset = {p["ticker"] for p in target}
                    for lot in [l for l in L["lots"] if l["sleeve"] == "core" and l["status"] == "open" and l["ticker"] not in tset]:
                        _sell(L, broker, today, lot, "left the weekly top five")
                    have = {l["ticker"] for l in live("core") if l["status"] != "closing"}
                    for p in target:
                        if p["ticker"] not in have:
                            cash -= _buy(L, broker, today, "core", p["ticker"], start * CORE_LOT, p.get("price"), cash)
                    L["last_core_week"] = wk
                    _log(L, today, f"core rebalance {wk}: target {sorted(tset)}")
                L["last_trade_date"] = today
        out = _summary(L, broker, today, note)
        path.write_text(json.dumps(L, indent=1))
        return out
    except Exception as e:   # the paper book must never take the daily brief down with it
        return {"enabled": False, "note": f"paper step failed: {type(e).__name__}: {str(e)[:160]}"}
