"""Sharia monitor — daily 10-Q check, weekly full scan, drift radar.

Two scheduled jobs land here:

DAILY (09:00 Dubai):
  For every symbol the user holds an OPEN position in (user_positions),
  check SEC EDGAR for a new 10-Q filed since the last verification. If
  found, re-verify and emit a compliance_alerts row + Telegram alert when
  the tier or status changed.

WEEKLY (Saturday 10:00 Dubai):
  Re-verify every enabled stock in the watchlist. Same diff/alert logic
  but applied broadly. Also drives the Sharia Drift Radar — any symbol
  whose last 4 quarterly debt ratios are rising at >=2pp/quarter AND whose
  current ratio is within 3pp of 30% breach gets a DRIFT_WARN alert.

Both jobs are safe to call manually (the scheduler binds them) and write
their decisions to compliance_alerts. The Telegram side reads that table
and renders alerts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from core.logger import get_logger
from db.repos import positions as positions_repo
from db.repos import sharia as sharia_repo
from db.repos import stocks as stocks_repo
from sharia.aaoifi import RiskTier, ShariaStatus, severity_for_status, is_drift_warning
from sharia.verifier import verify, VerificationResult

log = get_logger("sharia.monitor")


@dataclass
class MonitorReport:
    job: str                 # "daily" | "weekly" | "drift"
    started_at: str
    finished_at: str
    checked: list[str]
    re_verified: list[str]
    tier_changes: list[dict]
    drift_warnings: list[dict]
    errors: list[dict]


# --------------------------- DAILY: 10-Q sweep ---------------------------

def run_daily_check(
    *,
    fetch_yfinance_info=None,
    fetch_company_facts=None,
    fetch_market_cap=None,
    fetch_latest_filing_date=None,
) -> MonitorReport:
    """Check user_positions for new 10-Q filings and re-verify when found.

    The fetch_* callables are injected so the scheduler can pass real
    yfinance/SEC clients while tests can pass fakes. Defaults wire to the
    real data layer.
    """
    if fetch_yfinance_info is None:
        from data.sec_edgar import company_facts as _facts  # noqa
        fetch_yfinance_info = _yf_info_for
    if fetch_company_facts is None:
        from data.sec_edgar import company_facts as _facts2
        fetch_company_facts = _facts2
    if fetch_market_cap is None:
        fetch_market_cap = _market_cap_for
    if fetch_latest_filing_date is None:
        from data.sec_edgar import latest_filing_date_for
        fetch_latest_filing_date = lambda s: latest_filing_date_for(s, form="10-Q")

    started = _now()
    checked: list[str] = []
    re_verified: list[str] = []
    tier_changes: list[dict] = []
    drift_warnings: list[dict] = []
    errors: list[dict] = []

    open_syms = positions_repo.open_symbols()
    for sym in open_syms:
        checked.append(sym)
        try:
            latest_filing = fetch_latest_filing_date(sym)
            previous = sharia_repo.latest_ratios(sym)
            previous_filing = (previous or {}).get("filing_date")
            # Skip if we've already processed this filing
            if latest_filing and previous_filing == latest_filing:
                continue

            res = verify(
                sym,
                market_cap=fetch_market_cap(sym),
                yfinance_info=fetch_yfinance_info(sym),
                company_facts=fetch_company_facts(sym),
                persist=True,
            )
            re_verified.append(sym)
            tc = _diff_and_alert(sym, previous, res)
            if tc:
                tier_changes.append(tc)
            if res.drift_warning:
                drift_warnings.append({"symbol": sym, "notes": res.notes})
                _emit_drift_alert(sym, res)
        except Exception as exc:
            log.warning("daily monitor failed", extra={"symbol": sym, "err": str(exc)})
            errors.append({"symbol": sym, "err": str(exc)})

    return MonitorReport(
        job="daily",
        started_at=started,
        finished_at=_now(),
        checked=checked,
        re_verified=re_verified,
        tier_changes=tier_changes,
        drift_warnings=drift_warnings,
        errors=errors,
    )


# --------------------------- WEEKLY: full scan ---------------------------

def run_weekly_full_scan(
    *,
    symbols: Optional[Iterable[str]] = None,
    fetch_yfinance_info=None,
    fetch_company_facts=None,
    fetch_market_cap=None,
) -> MonitorReport:
    """Re-verify every enabled stock and re-check drift on each one."""
    if fetch_yfinance_info is None:
        fetch_yfinance_info = _yf_info_for
    if fetch_company_facts is None:
        from data.sec_edgar import company_facts as _facts
        fetch_company_facts = _facts
    if fetch_market_cap is None:
        fetch_market_cap = _market_cap_for

    started = _now()
    if symbols is None:
        symbols = [s.symbol for s in stocks_repo.list_all(enabled_only=True)]

    checked: list[str] = []
    re_verified: list[str] = []
    tier_changes: list[dict] = []
    drift_warnings: list[dict] = []
    errors: list[dict] = []

    for sym in symbols:
        checked.append(sym)
        try:
            previous = sharia_repo.latest_ratios(sym)
            res = verify(
                sym,
                market_cap=fetch_market_cap(sym),
                yfinance_info=fetch_yfinance_info(sym),
                company_facts=fetch_company_facts(sym),
                persist=True,
            )
            re_verified.append(sym)
            tc = _diff_and_alert(sym, previous, res)
            if tc:
                tier_changes.append(tc)
            if res.drift_warning:
                drift_warnings.append({"symbol": sym, "notes": res.notes})
                _emit_drift_alert(sym, res)
        except Exception as exc:
            log.warning("weekly monitor failed",
                        extra={"symbol": sym, "err": str(exc)})
            errors.append({"symbol": sym, "err": str(exc)})

    return MonitorReport(
        job="weekly",
        started_at=started,
        finished_at=_now(),
        checked=checked,
        re_verified=re_verified,
        tier_changes=tier_changes,
        drift_warnings=drift_warnings,
        errors=errors,
    )


# --------------------------- diff + alert helpers -----------------------

def _diff_and_alert(sym: str, previous: dict | None,
                    new: VerificationResult) -> dict | None:
    """Compare previous ratios row vs new VerificationResult and emit alerts."""
    new_status = new.status.value
    new_tier = new.overall_tier.value if new.overall_tier else None
    prev_status = (previous or {}).get("sharia_status")
    prev_tier = (previous or {}).get("risk_tier")

    status_changed = previous is not None and prev_status != new_status
    tier_changed = previous is not None and prev_tier != new_tier

    if not (status_changed or tier_changed):
        return None

    severity = severity_for_status(new.status)
    if status_changed:
        sharia_repo.insert_alert(
            symbol=sym,
            alert_type="STATUS_CHANGE",
            old_value=prev_status,
            new_value=new_status,
            severity=severity,
        )
    if tier_changed:
        sharia_repo.insert_alert(
            symbol=sym,
            alert_type="TIER_CHANGE",
            old_value=prev_tier,
            new_value=new_tier,
            severity=severity,
        )
    return {
        "symbol": sym,
        "old_status": prev_status,
        "new_status": new_status,
        "old_tier": prev_tier,
        "new_tier": new_tier,
        "severity": severity,
    }


def _emit_drift_alert(sym: str, res: VerificationResult) -> None:
    """Idempotent drift alert — only fire once per quarter for a given symbol.

    We treat the drift signal as "informational" (severity WARN) — Telegram
    will surface it but it does not change the symbol's status.
    """
    last_alerts = sharia_repo.alerts_for_symbol(sym, limit=10)
    # Suppress if a DRIFT_WARN was emitted for the same filing_date
    new_filing = res.ratios.inputs.filing_date if res.ratios else None
    for a in last_alerts:
        if a.get("alert_type") == "DRIFT_WARN" and (a.get("new_value") or "") == (new_filing or ""):
            return
    sharia_repo.insert_alert(
        symbol=sym,
        alert_type="DRIFT_WARN",
        old_value=None,
        new_value=new_filing,
        severity="WARN",
    )


# --------------------------- default fetcher wrappers --------------------

def _market_cap_for(symbol: str) -> float | None:
    info = _yf_info_for(symbol)
    if not info:
        return None
    try:
        return float(info.get("marketCap")) if info.get("marketCap") else None
    except Exception:
        return None


def _yf_info_for(symbol: str) -> dict | None:
    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(symbol)
        return t.info if t else None
    except Exception as exc:
        log.warning("yfinance info failed",
                    extra={"symbol": symbol, "err": str(exc)})
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
