"""SEC EDGAR client — free, no API key (User-Agent header required).

Three primary uses:
1. Look up CIK for a ticker.
2. Fetch the latest filings of given types (10-Q, 10-K, 8-K).
3. Pull the structured "company facts" XBRL JSON for ratio computation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from config.settings import settings
from core import cache
from core.logger import get_logger

log = get_logger("data.sec")

EDGAR_HEADERS = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"


@dataclass
class Filing:
    accession: str
    form: str            # 10-Q, 10-K, 8-K, etc.
    filing_date: str
    period_of_report: str | None
    primary_document: str | None
    primary_doc_url: str | None


# ------------------------------- CIK lookup ------------------------------

def cik_for_ticker(symbol: str) -> str | None:
    """Return zero-padded 10-digit CIK or None."""
    cached = cache.get("sec", "tickers_index", cache.TTL_SHARIA)
    if cached is None:
        try:
            import requests  # type: ignore
            r = requests.get(TICKERS_JSON, headers=EDGAR_HEADERS, timeout=15)
            r.raise_for_status()
            cached = r.json()
            cache.set_("sec", "tickers_index", cached)
        except Exception as exc:
            log.warning("sec ticker index failed", extra={"err": str(exc)})
            return None

    sym = symbol.upper()
    for entry in cached.values():
        if entry.get("ticker", "").upper() == sym:
            return f"{int(entry['cik_str']):010d}"
    return None


# ------------------------------ submissions ------------------------------

def latest_filings(
    symbol: str,
    *,
    forms: Iterable[str] = ("10-Q", "10-K", "8-K"),
    limit: int = 8,
    use_cache: bool = True,
) -> list[Filing]:
    cik = cik_for_ticker(symbol)
    if not cik:
        return []
    cache_key = f"submissions|{cik}|{','.join(sorted(forms))}|{limit}"
    if use_cache:
        cached = cache.get("sec", cache_key, cache.TTL_SEC_FILINGS)
        if cached is not None:
            return cached

    url = SUBMISSIONS_URL.format(cik10=cik)
    try:
        import requests  # type: ignore
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        log.warning("sec submissions failed", extra={"symbol": symbol, "err": str(exc)})
        return []

    recent = payload.get("filings", {}).get("recent", {}) or {}
    accessions = recent.get("accessionNumber") or []
    forms_arr = recent.get("form") or []
    dates = recent.get("filingDate") or []
    reports = recent.get("reportDate") or []
    primary_docs = recent.get("primaryDocument") or []

    accepted = set(forms)
    out: list[Filing] = []
    for i, form in enumerate(forms_arr):
        if form not in accepted:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        accession_nodash = accession.replace("-", "")
        primary_doc = primary_docs[i] if i < len(primary_docs) else None
        primary_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{primary_doc}"
            if primary_doc else None
        )
        out.append(Filing(
            accession=accession,
            form=form,
            filing_date=dates[i] if i < len(dates) else "",
            period_of_report=reports[i] if i < len(reports) else None,
            primary_document=primary_doc,
            primary_doc_url=primary_url,
        ))
        if len(out) >= limit:
            break

    if use_cache:
        cache.set_("sec", cache_key, out)
    return out


# ------------------------------ company facts ----------------------------

def company_facts(symbol: str, use_cache: bool = True) -> dict | None:
    """XBRL companyfacts JSON. Used by sharia/ratios.py to compute AAOIFI ratios."""
    cik = cik_for_ticker(symbol)
    if not cik:
        return None
    cache_key = f"facts|{cik}"
    if use_cache:
        cached = cache.get("sec", cache_key, cache.TTL_SEC_FILINGS)
        if cached is not None:
            return cached
    url = COMPANY_FACTS_URL.format(cik10=cik)
    try:
        import requests  # type: ignore
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("sec company facts failed", extra={"symbol": symbol, "err": str(exc)})
        return None
    if use_cache:
        cache.set_("sec", cache_key, data)
    return data


def latest_filing_date_for(symbol: str, form: str = "10-Q") -> str | None:
    filings = latest_filings(symbol, forms=(form,), limit=1)
    return filings[0].filing_date if filings else None
