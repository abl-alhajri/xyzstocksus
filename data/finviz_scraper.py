"""Polite Finviz scraper for symbol news.

Fallback when yfinance news is missing. Light HTML parsing only — no JS, no
authenticated endpoints. Cached for 30min per symbol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from core import cache
from core.logger import get_logger

log = get_logger("data.finviz")

NEWS_URL = "https://finviz.com/quote.ashx?t={symbol}"
USER_AGENT = "Mozilla/5.0 (XYZStocksUS news fetcher)"


@dataclass
class NewsItem:
    headline: str
    source: str | None
    url: str | None
    published: str | None


def fetch_news(symbol: str, *, use_cache: bool = True, limit: int = 20) -> List[NewsItem]:
    sym = symbol.upper()
    if use_cache:
        cached = cache.get("news", sym, cache.TTL_NEWS)
        if cached is not None:
            return cached[:limit]

    try:
        import requests  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
        r = requests.get(
            NEWS_URL.format(symbol=sym),
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as exc:
        log.warning("finviz fetch failed", extra={"symbol": sym, "err": str(exc)})
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", id="news-table")
    items: List[NewsItem] = []
    if not table:
        return []
    for row in table.find_all("tr"):
        a = row.find("a")
        if not a:
            continue
        headline = a.get_text(strip=True)
        href = a.get("href")
        td_time = row.find("td")
        published = td_time.get_text(strip=True) if td_time else None
        items.append(NewsItem(
            headline=headline,
            source=None,
            url=href,
            published=published,
        ))
        if len(items) >= limit:
            break

    if use_cache and items:
        cache.set_("news", sym, items)
    return items
