"""OpenInsider RSS — insider trades feed.

Powers the Insider Cluster Detector: 3+ insiders buying the same stock within
14 days, with ≥1 CFO/CEO → auto-elevate to deep analysis.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from config.thresholds import INSIDER_CLUSTER_DAYS, INSIDER_CLUSTER_MIN, INSIDER_CLUSTER_REQUIRE_OFFICER
from core import cache
from core.logger import get_logger

log = get_logger("data.openinsider")

OPENINSIDER_RSS = "https://openinsider.com/rss"

OFFICER_KEYWORDS = ("CEO", "Chief Executive", "CFO", "Chief Financial",
                    "President", "COO", "Chief Operating")


@dataclass
class InsiderTrade:
    symbol: str
    insider: str
    title: str
    transaction: str   # "P - Purchase" / "S - Sale" / etc.
    trade_date: str
    qty: int | None
    price: float | None
    value: float | None
    url: str | None


@dataclass
class Cluster:
    symbol: str
    buyer_count: int
    has_officer: bool
    earliest: str
    latest: str
    insiders: list[str]
    qualifies: bool


def fetch_recent_trades(*, use_cache: bool = True, limit: int = 200) -> list[InsiderTrade]:
    if use_cache:
        cached = cache.get("openinsider", "rss", cache.TTL_NEWS)
        if cached is not None:
            return cached[:limit]
    try:
        import feedparser  # type: ignore
        feed = feedparser.parse(OPENINSIDER_RSS)
    except Exception as exc:
        log.warning("openinsider rss failed", extra={"err": str(exc)})
        return []

    trades: list[InsiderTrade] = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        # OpenInsider RSS title pattern: "<TICKER> | <transaction> | <insider> ..."
        parts = [p.strip() for p in title.split("|")]
        symbol = parts[0] if parts else ""
        transaction = parts[1] if len(parts) > 1 else ""
        insider = parts[2] if len(parts) > 2 else ""
        # Best-effort title extraction from summary
        title_role = ""
        for kw in OFFICER_KEYWORDS:
            if kw.lower() in (insider + " " + summary).lower():
                title_role = kw
                break
        trades.append(InsiderTrade(
            symbol=(symbol or "").upper(),
            insider=insider,
            title=title_role,
            transaction=transaction,
            trade_date=_entry_date(entry),
            qty=None,
            price=None,
            value=None,
            url=entry.get("link"),
        ))
    if use_cache and trades:
        cache.set_("openinsider", "rss", trades)
    return trades


def detect_clusters(
    trades: Iterable[InsiderTrade] | None = None,
    *,
    days: int = INSIDER_CLUSTER_DAYS,
    min_buyers: int = INSIDER_CLUSTER_MIN,
    require_officer: bool = INSIDER_CLUSTER_REQUIRE_OFFICER,
) -> list[Cluster]:
    """Group purchases by symbol within the lookback window and flag qualifying clusters."""
    if trades is None:
        trades = fetch_recent_trades()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_symbol: dict[str, list[InsiderTrade]] = defaultdict(list)
    for t in trades:
        if not t.symbol:
            continue
        if "purchase" not in (t.transaction or "").lower():
            continue
        ts = _parse_iso(t.trade_date)
        if ts is None or ts < cutoff:
            continue
        by_symbol[t.symbol].append(t)

    clusters: list[Cluster] = []
    for sym, group in by_symbol.items():
        unique_buyers = {t.insider for t in group if t.insider}
        if len(unique_buyers) < min_buyers:
            continue
        has_officer = any(t.title for t in group)
        qualifies = (not require_officer) or has_officer
        dates = sorted(t.trade_date for t in group)
        clusters.append(Cluster(
            symbol=sym,
            buyer_count=len(unique_buyers),
            has_officer=has_officer,
            earliest=dates[0],
            latest=dates[-1],
            insiders=sorted(unique_buyers),
            qualifies=qualifies,
        ))

    clusters.sort(key=lambda c: (c.qualifies, c.buyer_count), reverse=True)
    return clusters


def _entry_date(entry) -> str:
    for k in ("published", "updated"):
        v = entry.get(k)
        if v:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                continue
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
