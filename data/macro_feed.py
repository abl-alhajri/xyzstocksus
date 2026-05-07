"""Macro context — Fed RSS, FOMC calendar, Trump posts.

All sources are free and public. Fetches are aggressively cached (6h) and
de-duplicated against macro_quotes / macro_events on the (speaker, date,
source_url) and (date, event_type, description) unique constraints.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from core import cache
from core.logger import get_logger
from db.connection import get_conn

log = get_logger("data.macro")

FED_SPEECHES_URL = "https://www.federalreserve.gov/feeds/speeches.xml"
FED_PRESS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
TRUMP_RSS_URL_DEFAULT = "https://rss.app/feeds/trump-truth-social.xml"  # placeholder

HAWKISH_HINTS = (
    "raise", "tighten", "restrictive", "elevated", "above target", "hawk",
    "higher for longer", "persistent", "balance of risks tilted", "premature",
    "too high", "vigilant", "more rate hikes",
)
DOVISH_HINTS = (
    "cut", "ease", "accommodative", "soft landing", "moderating", "dovish",
    "below target", "approaching target", "patient", "stable", "transitory",
    "loosen", "rate cut",
)


@dataclass
class MacroQuote:
    speaker: str
    tier: int
    venue: str | None
    date: str
    quote_text: str
    sentiment: str | None
    source_url: str | None


@dataclass
class MacroEvent:
    date: str
    event_type: str
    description: str
    expected_impact: str  # HIGH | MEDIUM | LOW


# ----------------------------- Fed speeches ------------------------------

def fetch_fed_speeches(use_cache: bool = True) -> list[MacroQuote]:
    return _fetch_rss_quotes(
        url=FED_SPEECHES_URL,
        speaker_default="FederalReserve",
        venue_default="Speech",
        cache_key="fed_speeches",
        use_cache=use_cache,
    )


def fetch_fed_press(use_cache: bool = True) -> list[MacroQuote]:
    return _fetch_rss_quotes(
        url=FED_PRESS_URL,
        speaker_default="FOMC",
        venue_default="Press release",
        cache_key="fed_press",
        use_cache=use_cache,
    )


def _fetch_rss_quotes(*, url: str, speaker_default: str, venue_default: str,
                     cache_key: str, use_cache: bool) -> list[MacroQuote]:
    if use_cache:
        cached = cache.get("macro", cache_key, cache.TTL_MACRO)
        if cached is not None:
            return cached

    try:
        import feedparser  # type: ignore
        feed = feedparser.parse(url)
    except Exception as exc:
        log.warning("rss fetch failed", extra={"url": url, "err": str(exc)})
        return []

    quotes: list[MacroQuote] = []
    for entry in feed.entries[:30]:
        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip()
        text = " ".join(p for p in (title, summary) if p)
        if not text:
            continue
        speaker = _detect_speaker(text) or speaker_default
        date = _entry_date(entry)
        sentiment = classify_sentiment(text)
        link = entry.get("link") or None
        quotes.append(MacroQuote(
            speaker=speaker,
            tier=_tier_for_speaker(speaker),
            venue=venue_default,
            date=date,
            quote_text=text[:1500],
            sentiment=sentiment,
            source_url=link,
        ))

    persist_quotes(quotes)
    if use_cache:
        cache.set_("macro", cache_key, quotes)
    return quotes


def _detect_speaker(text: str) -> str | None:
    """Light heuristic. Real attribution is upstream; this just tags Powell."""
    lt = text.lower()
    if "powell" in lt:
        return "Powell"
    if "yellen" in lt:
        return "Yellen"
    if "fomc" in lt:
        return "FOMC"
    return None


def _tier_for_speaker(speaker: str) -> int:
    """1 = highest impact (Powell, FOMC). 2 = governors. 3 = regional / staff."""
    s = (speaker or "").lower()
    if s in ("powell", "fomc"):
        return 1
    if s in ("yellen", "federalreserve", "trump"):
        return 2
    return 3


def _entry_date(entry) -> str:
    for key in ("published", "updated"):
        v = entry.get(key)
        if v:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                continue
    return _now()


def classify_sentiment(text: str) -> str:
    """Tiny lexicon-based classifier. Phase 2 will add embedding-based scoring."""
    lt = text.lower()
    h = sum(1 for w in HAWKISH_HINTS if w in lt)
    d = sum(1 for w in DOVISH_HINTS if w in lt)
    if h > d and h > 0:
        return "HAWKISH"
    if d > h and d > 0:
        return "DOVISH"
    return "NEUTRAL"


# ----------------------------- FOMC calendar -----------------------------

def fetch_fomc_calendar(use_cache: bool = True) -> list[MacroEvent]:
    if use_cache:
        cached = cache.get("macro", "fomc_calendar", cache.TTL_MACRO)
        if cached is not None:
            return cached

    try:
        import requests  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
        r = requests.get(FOMC_CALENDAR_URL, timeout=10,
                         headers={"User-Agent": "XYZStocksUS"})
        r.raise_for_status()
    except Exception as exc:
        log.warning("fomc calendar failed", extra={"err": str(exc)})
        return []

    events: list[MacroEvent] = []
    soup = BeautifulSoup(r.text, "html.parser")
    # Page lists meeting blocks; we extract any heading containing 'Meeting'
    for div in soup.select(".fomc-meeting__month, .panel-default, .panel"):
        text = div.get_text(" ", strip=True)
        if not text or "Meeting" not in text:
            continue
        events.append(MacroEvent(
            date=_now()[:10],  # actual date parsing is non-trivial; placeholder
            event_type="FOMC",
            description=text[:300],
            expected_impact="HIGH",
        ))

    persist_events(events)
    if use_cache:
        cache.set_("macro", "fomc_calendar", events)
    return events


# ----------------------------- Trump posts -------------------------------

def fetch_trump_posts(*, url: str | None = None, use_cache: bool = True) -> list[MacroQuote]:
    """Configurable rss.app feed for Truth Social. Tier 2 by default.

    Empty list is the safe default if the feed isn't configured — the system
    must keep running even when this source is dark.
    """
    feed_url = url or TRUMP_RSS_URL_DEFAULT
    if use_cache:
        cached = cache.get("macro", "trump_rss", cache.TTL_MACRO)
        if cached is not None:
            return cached
    try:
        import feedparser  # type: ignore
        feed = feedparser.parse(feed_url)
    except Exception as exc:
        log.warning("trump rss failed", extra={"err": str(exc)})
        return []

    quotes: list[MacroQuote] = []
    for entry in feed.entries[:50]:
        text = entry.get("title") or entry.get("summary") or ""
        if not text:
            continue
        quotes.append(MacroQuote(
            speaker="Trump",
            tier=2,
            venue="TruthSocial",
            date=_entry_date(entry),
            quote_text=text[:1500],
            sentiment=classify_sentiment(text),
            source_url=entry.get("link"),
        ))

    persist_quotes(quotes)
    if use_cache:
        cache.set_("macro", "trump_rss", quotes)
    return quotes


# ----------------------------- persistence -------------------------------

def persist_quotes(quotes: Iterable[MacroQuote]) -> int:
    n = 0
    with get_conn() as conn:
        for q in quotes:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO macro_quotes
                      (speaker, tier, venue, date, quote_text, sentiment, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (q.speaker, q.tier, q.venue, q.date, q.quote_text,
                     q.sentiment, q.source_url),
                )
                n += 1
            except Exception as exc:  # pragma: no cover
                log.warning("quote insert failed", extra={"err": str(exc)})
    return n


def persist_events(events: Iterable[MacroEvent]) -> int:
    n = 0
    with get_conn() as conn:
        for e in events:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO macro_events
                      (date, event_type, description, expected_impact)
                    VALUES (?, ?, ?, ?)
                    """,
                    (e.date, e.event_type, e.description, e.expected_impact),
                )
                n += 1
            except Exception as exc:  # pragma: no cover
                log.warning("event insert failed", extra={"err": str(exc)})
    return n


def recent_quotes(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM macro_quotes ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def upcoming_events(days: int = 14) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM macro_events
            WHERE date >= date('now')
              AND date <= date('now', ?)
            ORDER BY date ASC
            """,
            (f"+{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
