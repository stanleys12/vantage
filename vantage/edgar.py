"""SEC EDGAR: 13F holdings for tracked gurus (latest vs prior quarter) with honest staleness."""
from __future__ import annotations
import re, time, json
from datetime import date
from xml.etree import ElementTree as ET
import requests
from .config import UA, GURUS, CACHE

S = requests.Session(); S.headers.update(UA)


def _get(url, **kw):
    for i in range(3):
        r = S.get(url, timeout=30, **kw)
        if r.status_code == 200:
            return r
        time.sleep(1.5 * (i + 1))
    r.raise_for_status()


def _13f_filings(cik: str, n: int = 2) -> list[dict]:
    j = _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    rec = j["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form in ("13F-HR", "13F-HR/A") and len(out) < n:
            # prefer original over amendment of same period
            out.append({"acc": rec["accessionNumber"][i], "filed": rec["filingDate"][i],
                        "period": rec["reportDate"][i], "form": form})
    return out, j.get("name")


def _infotable(cik: str, acc: str) -> list[dict]:
    acc_nd = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nd}/"
    idx = _get(base).text
    xmls = re.findall(r'href="([^"]+\.xml)"', idx)
    cand = [x for x in xmls if "primary_doc" not in x.lower()]
    rows = []
    for x in cand:
        url = x if x.startswith("http") else ("https://www.sec.gov" + x if x.startswith("/") else base + x)
        try:
            txt = _get(url).text
        except Exception:
            continue
        if "infoTable" not in txt and "informationTable" not in txt:
            continue
        root = ET.fromstring(txt)
        ns = {"n": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
        tag = (lambda t: f"n:{t}" if ns else t)
        for it in root.iter(f"{{{ns['n']}}}infoTable" if ns else "infoTable"):
            g = lambda t: (it.find(tag(t), ns).text if it.find(tag(t), ns) is not None else None)
            sh = it.find(tag("shrsOrPrnAmt"), ns)
            shares = sh.find(tag("sshPrnamt"), ns).text if sh is not None and sh.find(tag("sshPrnamt"), ns) is not None else None
            try:
                rows.append({"issuer": (g("nameOfIssuer") or "").strip(), "cusip": g("cusip"),
                             "value": float(g("value") or 0), "shares": float(shares or 0),
                             "put_call": (it.find(tag("putCall"), ns).text if it.find(tag("putCall"), ns) is not None else None)})
            except Exception:
                continue
        if rows:
            break
    # 13F values are in thousands USD (pre-2023 ) or dollars (post-2023 rule change); normalize by scale sanity
    tot = sum(r["value"] for r in rows)
    if tot and tot < 5e7:   # looks like thousands
        for r in rows:
            r["value"] *= 1000
    return rows


def guru_snapshot(max_holdings: int = 15) -> dict:
    """For each guru: filed/period dates, staleness, top holdings, and changes vs prior 13F."""
    out = {}
    for name, cik in GURUS.items():
        try:
            fl, entity = _13f_filings(cik, n=2)
            if not fl:
                continue
            cur = _infotable(cik, fl[0]["acc"])
            prev = _infotable(cik, fl[1]["acc"]) if len(fl) > 1 else []
            if not cur:
                continue
            agg = lambda rows: {}
            def agg(rows):
                d = {}
                for r in rows:
                    if r["put_call"]:
                        continue
                    k = r["issuer"].upper()
                    d.setdefault(k, {"issuer": r["issuer"], "value": 0.0, "shares": 0.0})
                    d[k]["value"] += r["value"]; d[k]["shares"] += r["shares"]
                return d
            c, p = agg(cur), agg(prev)
            total = sum(v["value"] for v in c.values()) or 1
            top = sorted(c.values(), key=lambda v: -v["value"])[:max_holdings]
            for t in top:
                t["pct"] = round(t["value"] / total * 100, 1)
            new = [c[k]["issuer"] for k in c if k not in p][:10]
            exited = [p[k]["issuer"] for k in p if k not in c][:10]
            adds, trims = [], []
            for k in c:
                if k in p and p[k]["shares"] > 0:
                    ch = c[k]["shares"] / p[k]["shares"] - 1
                    if ch > 0.15: adds.append((c[k]["issuer"], round(ch * 100)))
                    elif ch < -0.15: trims.append((c[k]["issuer"], round(ch * 100)))
            adds.sort(key=lambda x: -x[1]); trims.sort(key=lambda x: x[1])
            stale = (date.today() - date.fromisoformat(fl[0]["period"])).days
            is_stale = stale > STALE_DAYS
            out[name] = {"entity": entity, "filed": fl[0]["filed"], "period": fl[0]["period"],
                         "days_since_period": stale, "stale": is_stale, "positions": len(c), "aum_13f": round(total / 1e9, 2),
                         "top": top, "new": new, "exited": exited, "adds": adds[:8], "trims": trims[:8],
                         "note": (f"STALE: latest 13F covers {fl[0]['period']} ({stale} days ago) — filer inactive or late; excluded from matching."
                                  if is_stale else f"Positions as of {fl[0]['period']} (filed {fl[0]['filed']}); {stale} days stale by law.")}
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
        time.sleep(0.4)   # SEC fair-access: <=10 req/s; we stay far below
    (CACHE / "gurus.json").write_text(json.dumps(out, indent=1, default=str))
    return out


FUND_WORDS = ("ETF", "SPDR", "ISHARES", "VANGUARD", "PROSHARES", "INVESCO", "SELECT SECTOR", "INDEX FD", " FUND", "FD ")
STALE_DAYS = 150   # a 13F older than this is a dead/late filer: excluded from matching, labelled in the brief


def match_guru_holdings(gurus: dict, names: dict[str, str]) -> dict[str, list[str]]:
    """Map 13F issuer names to universe tickers. Exact normalized-name match, else a unique match on
    the first two words. ETF/fund issuers are skipped (they are not the sponsor's stock — e.g. the
    SPDR S&P 500 trust is not State Street). Stale filers are skipped entirely.
    names: ticker -> company name. Returns ticker -> ["Guru (pct%, NEW/ADD +x%)"...]."""
    def norm(s):
        s = re.sub(r"[^A-Z0-9 ]", " ", s.upper())
        s = re.sub(r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|HOLDINGS|HLDGS|GROUP|CLASS|CL|COM|NEW|THE|DEL|SA|NV|LP|TRUST|SHS|ORD|COMMON|STOCK|A|B|C)\b", " ", s)
        return " ".join(s.split())
    full, two = {}, {}
    for t, n in names.items():
        k = norm(n)
        if not k:
            continue
        full[k] = t
        w = k.split(" ")
        key = " ".join(w[:2]) if len(w) >= 2 else (w[0] if len(w[0]) >= 5 else None)
        if key:
            two.setdefault(key, set()).add(t)
    hits = {}
    for g, d in gurus.items():
        if "top" not in d or d.get("stale"):
            continue
        newset = {norm(x) for x in d.get("new", [])}
        adds = {norm(a): pct for a, pct in d.get("adds", [])}
        for h in d["top"]:
            raw = " " + re.sub(r"[^A-Z0-9 ]", " ", h["issuer"].upper()) + " "
            if any(fw in raw for fw in FUND_WORDS):
                continue
            k = norm(h["issuer"])
            t = full.get(k)
            if not t:
                w = k.split(" ")
                key = " ".join(w[:2]) if len(w) >= 2 else (w[0] if w and len(w[0]) >= 5 else None)
                c = two.get(key, set()) if key else set()
                t = next(iter(c)) if len(c) == 1 else None
            if t:
                tag = f"{g.split(' /')[0]} ({h['pct']}%"
                if k in newset: tag += ", NEW"
                elif k in adds: tag += f", +{adds[k]}%"
                tag += ")"
                hits.setdefault(t, []).append(tag)
    return hits
