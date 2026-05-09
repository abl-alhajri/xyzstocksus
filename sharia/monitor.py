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

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

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


# --------------------------- on-demand full refresh ---------------------

def run_full_refresh(
    *,
    progress_cb: Callable[[str], None] | None = None,
    every: int = 5,
    fetch_yfinance_info=None,
    fetch_company_facts=None,
    fetch_market_cap=None,
) -> dict:
    """Re-verify every enabled ticker, with progress callbacks + cache fallback.

    Backs the /refresh_sharia admin command. Differences vs run_weekly_full_scan:
      - emits per-N-ticker progress via `progress_cb(str)` (chat updates)
      - on yfinance + SEC double-failure, falls back to the most recent cached
        row in financial_ratios_history rather than poisoning the DB with
        zero/garbage values
      - structured `[sharia-refresh]` log lines so Railway logs are greppable
    """
    if fetch_yfinance_info is None:
        fetch_yfinance_info = _yf_info_for
    if fetch_company_facts is None:
        from data.sec_edgar import company_facts as _facts
        fetch_company_facts = _facts
    if fetch_market_cap is None:
        fetch_market_cap = _market_cap_for

    started = time.time()
    syms = [s.symbol for s in stocks_repo.list_all(enabled_only=True)]
    total = len(syms)

    log.info("[sharia-refresh] Starting full verification: %d tickers", total)
    if progress_cb:
        try:
            progress_cb(f"<b>[sharia-refresh]</b> Starting: {total} tickers")
        except Exception:
            pass

    by_status: dict[str, int] = {"HALAL": 0, "MIXED": 0, "HARAM": 0, "PENDING": 0}
    status_changes: list[dict] = []
    used_cache: list[str] = []
    errors: list[dict] = []

    for i, sym in enumerate(syms, 1):
        try:
            previous = sharia_repo.latest_ratios(sym)
            prev_status = (previous or {}).get("sharia_status")

            info = fetch_yfinance_info(sym)
            facts = fetch_company_facts(sym)

            # Cache fallback when both live data sources are unavailable
            if info is None and facts is None:
                if previous:
                    cached_at = (previous.get("fetched_at") or "?")[:10]
                    log.warning("[sharia-refresh] %s: data sources failed, "
                                "using cached row from %s", sym, cached_at)
                    used_cache.append(sym)
                    if prev_status:
                        by_status[prev_status] = by_status.get(prev_status, 0) + 1
                    continue
                log.warning("[sharia-refresh] %s: no data and no cache — "
                            "skipping (INCOMPLETE, not persisted)", sym)
                errors.append({"symbol": sym, "reason": "no data + no cache"})
                continue

            mc = fetch_market_cap(sym)
            res = verify(sym, market_cap=mc, yfinance_info=info,
                         company_facts=facts, persist=True)
            new_status = res.status.value
            by_status[new_status] = by_status.get(new_status, 0) + 1

            if prev_status and prev_status != new_status:
                debt_old = (previous or {}).get("debt_ratio")
                debt_new = res.ratios.debt_ratio if res.ratios else None
                debt_str = ""
                if debt_old is not None and debt_new is not None:
                    debt_str = f" (debt: {debt_old*100:.0f}% → {debt_new*100:.0f}%)"
                log.info("[sharia-refresh] %s: %s → %s%s",
                         sym, prev_status, new_status, debt_str)
                status_changes.append({"symbol": sym,
                                       "old": prev_status, "new": new_status})
            else:
                log.info("[sharia-refresh] %s: %s (no change)", sym, new_status)

        except Exception as exc:
            log.warning("[sharia-refresh] %s: failed: %s", sym, exc)
            errors.append({"symbol": sym, "err": str(exc)})

        if progress_cb and i % every == 0 and i < total:
            try:
                progress_cb(f"Verified {i}/{total}…")
            except Exception:
                pass

    elapsed = time.time() - started
    summary = (
        f"✅ <b>[sharia-refresh] Done</b> in "
        f"{int(elapsed // 60)}m {int(elapsed % 60)}s\n"
        f"  HALAL: {by_status.get('HALAL', 0)}\n"
        f"  MIXED: {by_status.get('MIXED', 0)}\n"
        f"  HARAM: {by_status.get('HARAM', 0)}\n"
        f"  Status changes: {len(status_changes)}\n"
        f"  Used cache: {len(used_cache)}\n"
        f"  FAILED: {len(errors)}"
    )
    log.info("[sharia-refresh] Done in %dm %ds. Status changes: %d, "
             "errors: %d", int(elapsed // 60), int(elapsed % 60),
             len(status_changes), len(errors))
    if progress_cb:
        try:
            progress_cb(summary)
        except Exception:
            pass

    return {
        "total": total,
        "elapsed_sec": elapsed,
        "by_status": by_status,
        "status_changes": status_changes,
        "used_cache": used_cache,
        "errors": errors,
    }


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
        info = t.info if t else None
        # yfinance returns {} on rate-limit / silent failure; treat as None
        if not info:
            log.warning("[sharia] yfinance returned empty for %s", symbol)
            return None
        return info
    except Exception as exc:
        log.warning("[sharia] yfinance failed for %s: %s", symbol, exc)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
