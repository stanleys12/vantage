"""News aggregation from free RSS (Google News, Yahoo Finance, CNBC) with dedupe and weak sentiment."""
from __future__ import annotations
import re, time, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import feedparser

POS = {"beat", "beats", "surge", "surges", "record", "upgrade", "upgrades", "raises", "raised", "strong",
       "growth", "buyback", "profit", "profits", "outperform", "rally", "rallies", "soar", "soars", "jumps",
       "expands", "wins", "approval", "approved", "bullish", "accelerat", "exceed", "boost"}
NEG = {"miss", "misses", "plunge", "plunges", "downgrade", "downgrades", "cuts", "cut", "weak", "lawsuit",
       "probe", "investigation", "recall", "falls", "fall", "drop", "drops", "slump", "slumps", "bearish",
       "layoffs", "warns", "warning", "fraud", "halt", "decline", "declines", "loss", "losses", "tumble",
       "sinks", "guidance cut", "sec charges", "delay", "delays", "resign"}


def _sent(title: str) -> int:
    t = title.lower()
    p = sum(1 for w in POS if w in t); n = sum(1 for w in NEG if w in t)
    return (p > n) - (n > p)


def _parse(url: str, source: str, max_age_h: int = 72) -> list[dict]:
    try:
        f = feedparser.parse(url)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
    items = []
    for e in f.entries:
        pub = None
        for k in ("published_parsed", "updated_parsed"):
            if getattr(e, k, None):
                pub = datetime(*getattr(e, k)[:6], tzinfo=timezone.utc); break
        if pub and pub < cutoff:
            continue
        title = re.sub(r"\s+", " ", getattr(e, "title", "")).strip()
        if not title:
            continue
        src = source
        if source == "google" and " - " in title:
            title, src = title.rsplit(" - ", 1)
        items.append({"title": title, "source": src, "link": getattr(e, "link", ""),
                      "published": pub.isoformat(timespec="minutes") if pub else None,
                      "age_h": round((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 1) if pub else None,
                      "sent": _sent(title)})
    return items


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x.get("age_h") if x.get("age_h") is not None else 999):
        key = hashlib.md5(re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60].encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key); out.append(it)
    return out


def ticker_news(ticker: str, name: str | None = None, limit: int = 12) -> dict:
    q = f'"{ticker}" stock' if not name else f'"{name}" OR "{ticker}" stock'
    items = _parse(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en", "google")
    items += _parse(f"https://finance.yahoo.com/rss/headline?s={ticker}", "yahoo")
    items = _dedupe(items)[:limit]
    n = len(items)
    score = round(sum(i["sent"] for i in items) / n, 2) if n else 0.0
    fresh = sum(1 for i in items if (i.get("age_h") or 99) <= 24)
    return {"items": items, "n": n, "fresh_24h": fresh, "sent_score": score}


def market_news(limit: int = 20) -> list[dict]:
    items = _parse("https://www.cnbc.com/id/100003114/device/rss/rss.html", "cnbc", 36)
    items += _parse("https://www.cnbc.com/id/10000664/device/rss/rss.html", "cnbc-markets", 36)
    items += _parse("https://news.google.com/rss/search?q=stock+market+today&hl=en-US&gl=US&ceid=US:en", "google", 24)
    items += _parse("https://news.google.com/rss/search?q=federal+reserve+OR+inflation+OR+treasury+yields&hl=en-US&gl=US&ceid=US:en", "google", 36)
    return _dedupe(items)[:limit]
