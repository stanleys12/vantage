"""Investable universe: S&P 500 constituents (live CSV, cached) + curated ETFs."""
from __future__ import annotations
import csv, io, json, time
import requests
from .config import SP500_CSV, ETFS, CACHE, UA

_CACHE = CACHE / "sp500.json"


def sp500(max_age_days: int = 7) -> dict[str, dict]:
    """ticker -> {name, sector}. Cached weekly."""
    if _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < max_age_days * 86400:
        return json.loads(_CACHE.read_text())
    try:
        r = requests.get(SP500_CSV, headers=UA, timeout=30); r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        out = {}
        for row in rows:
            sym = (row.get("Symbol") or row.get("symbol") or "").strip()
            if not sym:
                continue
            out[sym] = {"name": (row.get("Security") or row.get("Name") or sym).strip(),
                        "sector": (row.get("GICS Sector") or row.get("Sector") or "").strip()}
        if len(out) > 400:
            _CACHE.write_text(json.dumps(out))
            return out
    except Exception:
        pass
    if _CACHE.exists():
        return json.loads(_CACHE.read_text())
    raise RuntimeError("could not load S&P 500 universe and no cache present")


def full() -> dict[str, dict]:
    u = {t: {**v, "kind": "stock"} for t, v in sp500().items()}
    for t, n in ETFS.items():
        u[t] = {"name": n, "sector": "ETF", "kind": "etf"}
    return u
