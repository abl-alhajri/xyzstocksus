"""Top-level scan loop — wires data → indicators → prescreen → debate → persist.

A single `run_scan()` entry point covers all four daily scans. The Telegram
side calls this directly via /scan; the scheduler calls it on its cron.

Insider Cluster Detector is integrated here: any qualifying cluster on a
watchlist symbol is auto-elevated into the deep-analysis pool regardless of
heuristic score (per refined plan creative #6).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.base import AgentInput, AgentOutput
from agents.debate import DebateResult, run_debate_async
from config.agent_sets import SETS_BY_NAME
from config.settings import settings
from config.thresholds import (
    BTC_DUMP_PCT, BTC_DUMP_WINDOW_MIN,
    DEDUP_CONFIDENCE_JUMP, DEDUP_WINDOW_HOURS, MIN_CONFIDENCE_FOR_ALERT,
)
from core import budget_guard, dedup
from core.logger import get_logger
from core.price_filter import is_in_range, reason_out_of_range
from data import btc_feed, earnings_calendar, macro_feed, openinsider, prices
from db.repos import signals as signals_repo
from db.repos import stocks as stocks_repo
from indicators import correlation as corr_mod
from indicators.heuristic_score import score
from indicators.technical import summarize as tech_summary
from llm.prescreen_haiku import PrescreenCandidate, run as run_prescreen
from sharia.aaoifi import ShariaStatus

log = get_logger("core.orchestrator")


@dataclass
class ScanReport:
    started_at: str
    finished_at: str
    market_status: str
    btc_price: float | None
    btc_regime: str | None
    btc_dump_active: bool
    candidates_pool: int
    prescreen_pool: int
    deep_survivors: int
    debates: list[DebateResult] = field(default_factory=list)
    signals_recorded: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    excluded_by_price: list[str] = field(default_factory=list)


async def run_scan_async(*, allow_outside_hours: bool = True) -> ScanReport:
    """Execute one full scan. Returns a structured report."""
    started = _now()
    report = ScanReport(
        started_at=started, finished_at="",
        market_status="", btc_price=None, btc_regime=None,
        btc_dump_active=False, candidates_pool=0,
        prescreen_pool=0, deep_survivors=0,
    )

    # ------------------------------------------------------------------
    # 1. Concurrent data pulls
    # ------------------------------------------------------------------
    enabled = stocks_repo.list_all(enabled_only=True)
    enabled_syms = [s.symbol for s in enabled]
    report.candidates_pool = len(enabled_syms)

    btc_snap = btc_feed.fetch_spot(use_cache=True)
    btc_regime = btc_feed.classify_regime()
    if btc_snap:
        report.btc_price = btc_snap.price
    report.btc_regime = btc_regime.label

    btc_dump = btc_feed.is_dump(drop_pct=BTC_DUMP_PCT, window_min=BTC_DUMP_WINDOW_MIN)
    report.btc_dump_active = btc_dump
    if btc_dump:
        report.notes.append("BTC dump active — btc_full agents will be skipped")

    # Macro context (cached 6h)
    fed = macro_feed.fetch_fed_speeches(use_cache=True)
    fomc = macro_feed.fetch_fed_press(use_cache=True)
    trump = macro_feed.fetch_trump_posts(use_cache=True)
    macro_recent = [_quote_to_dict(q) for q in (fed[:5] + fomc[:5] + trump[:5])]
    upcoming = macro_feed.upcoming_events(days=14)

    # Insider clusters — auto-elevate anyone qualifying
    insider_clusters: dict[str, dict] = {}
    try:
        clusters = openinsider.detect_clusters()
        for c in clusters:
            if c.qualifies and c.symbol in {s.upper() for s in enabled_syms}:
                insider_clusters[c.symbol] = {
                    "buyer_count": c.buyer_count,
                    "has_officer": c.has_officer,
                    "earliest": c.earliest,
                    "latest": c.latest,
                    "insiders": c.insiders,
                }
    except Exception as exc:
        log.warning("insider clusters unavailable", extra={"err": str(exc)})

    # Prices — single batch
    fetch_period = "120d"
    fetch_interval = "1d"
    res = prices.fetch_history(enabled_syms + ["BTC-USD"],
                              period=fetch_period, interval=fetch_interval)

    # Price-band filter — drop symbols whose last close falls outside the
    # configured [MIN_STOCK_PRICE_USD, MAX_STOCK_PRICE_USD] window. Missing
    # frames pass through (we never silently drop on a fetch failure).
    price_filtered: list = []
    for stock in enabled:
        df = res.frames.get(stock.symbol)
        last_close: float | None = None
        if df is not None:
            try:
                last_close = float(df["Close"].iloc[-1])
            except Exception:
                last_close = None
        if last_close is not None and not is_in_range(last_close):
            report.excluded_by_price.append(stock.symbol)
            log.info(
                "[watchlist] Skipping %s: %s",
                stock.symbol, reason_out_of_range(last_close),
            )
            continue
        price_filtered.append(stock)
    if report.excluded_by_price:
        report.notes.append(
            f"Excluded by price: {', '.join(report.excluded_by_price)}"
        )
    enabled = price_filtered
    enabled_syms = [s.symbol for s in enabled]
    report.candidates_pool = len(enabled_syms)

    # ------------------------------------------------------------------
    # 2. Heuristic scores
    # ------------------------------------------------------------------
    btc_df = res.frames.get("BTC-USD")
    btc_closes: list[float] = []
    if btc_df is not None:
        try:
            btc_closes = [float(x) for x in btc_df["Close"].tolist()]
        except Exception:
            btc_closes = []

    candidates: list[tuple[stocks_repo.StockRow, dict, dict]] = []
    for stock in enabled:
        df = res.frames.get(stock.symbol)
        tech = tech_summary(df)
        stock_closes: list[float] = []
        if df is not None:
            try:
                stock_closes = [float(x) for x in df["Close"].tolist()]
            except Exception:
                pass
        btc_corr = corr_mod.btc_correlation_30d(stock_closes, btc_closes) if btc_closes else None

        is_btc_full = stock.agent_set == "btc_full"
        breakdown = score(
            tech=tech,
            btc_corr_30d=btc_corr,
            btc_regime=report.btc_regime,
            btc_beta=stock.btc_beta,
            is_btc_full=is_btc_full,
        )

        # Persist heuristic for the dashboard
        try:
            from db.repos.stocks import insert_heuristic
            insert_heuristic(
                stock.symbol,
                rsi=tech.rsi_14, macd=tech.macd, macd_signal=tech.macd_signal,
                volume_ratio=tech.volume_ratio_20d, btc_corr_30d=btc_corr,
                score=breakdown.total,
                raw={"breakdown": breakdown.__dict__,
                     "tech": tech.__dict__},
            )
        except Exception as exc:  # pragma: no cover
            log.warning("heuristic persist failed",
                        extra={"symbol": stock.symbol, "err": str(exc)})

        candidates.append((stock, breakdown.__dict__, tech.__dict__))

    # ------------------------------------------------------------------
    # 3. Top-N pool selection (with insider auto-elevation)
    # ------------------------------------------------------------------
    candidates_sorted = sorted(candidates, key=lambda c: c[1]["total"], reverse=True)
    top_pool = candidates_sorted[: settings.prescreen_top_n]
    pool_syms = {c[0].symbol for c in top_pool}

    for sym, _meta in insider_clusters.items():
        if sym not in pool_syms:
            extra = next((c for c in candidates if c[0].symbol == sym), None)
            if extra:
                top_pool.append(extra)
                pool_syms.add(sym)
                report.notes.append(f"{sym} elevated via Insider Cluster Detector")

    report.prescreen_pool = len(top_pool)

    # ------------------------------------------------------------------
    # 4. Earnings blackout flag
    # ------------------------------------------------------------------
    blackout: dict[str, bool] = {}
    for stock, _b, _t in top_pool:
        try:
            blackout[stock.symbol] = earnings_calendar.in_blackout(stock.symbol)
        except Exception:
            blackout[stock.symbol] = False

    # ------------------------------------------------------------------
    # 5. Haiku pre-screen
    # ------------------------------------------------------------------
    cands = [
        PrescreenCandidate(
            symbol=stock.symbol,
            sector=stock.sector,
            agent_set=stock.agent_set,
            sharia_status=stock.sharia_status,
            heuristic=breakdown_dict,
            last_price=tech_dict.get("last_close"),
            btc_regime=report.btc_regime,
            earnings_blackout=blackout.get(stock.symbol, False),
        )
        for stock, breakdown_dict, tech_dict in top_pool
    ]
    pre = run_prescreen(cands)
    report.deep_survivors = len(pre.survivors)
    if pre.blocked_reason:
        report.notes.append(f"Prescreen skipped: {pre.blocked_reason}")
        report.finished_at = _now()
        return report

    survivor_syms = {v.symbol for v in pre.survivors}

    # ------------------------------------------------------------------
    # 6. Multi-agent debate per survivor (concurrent)
    # ------------------------------------------------------------------
    survivor_inputs = [
        _build_agent_input(
            stock=stock, breakdown=bd, tech=t,
            btc_price=report.btc_price, btc_regime=report.btc_regime,
            btc_corr=corr_mod.btc_correlation_30d(
                [float(x) for x in (res.frames.get(stock.symbol, [])["Close"].tolist()
                                    if res.frames.get(stock.symbol) is not None else [])],
                btc_closes,
            ) if btc_closes else None,
            macro_recent=macro_recent,
            upcoming_events=upcoming,
            earnings_blackout=blackout.get(stock.symbol, False),
            insider_cluster=insider_clusters.get(stock.symbol),
        )
        for stock, bd, t in top_pool
        if stock.symbol in survivor_syms
    ]

    debate_tasks = [
        run_debate_async(
            inp,
            SETS_BY_NAME[inp.agent_set],
            skip_btc_full=btc_dump,
        )
        for inp in survivor_inputs
    ]
    if debate_tasks:
        debates = await asyncio.gather(*debate_tasks, return_exceptions=False)
        report.debates = list(debates)

    # ------------------------------------------------------------------
    # 7. Persist signals + push to Telegram
    # ------------------------------------------------------------------
    for d in report.debates:
        sid = _persist_debate_signal(d)
        if sid is not None:
            report.signals_recorded.append(sid)
            await _maybe_push_signal_async(d, sid, report.btc_price)

    # Reconcile quick_only flag (auto-flips at 75% monthly)
    try:
        budget_guard.reconcile_quick_only_flag()
    except Exception:
        pass

    report.finished_at = _now()
    return report


def _run_async_in_thread(coro_func, *args, **kwargs):
    """Create the coroutine INSIDE the worker thread so it never leaks if
    asyncio.run can't accept it. Used by run_scan() when the calling thread
    already has a running event loop (e.g. async Telegram handler)."""
    return asyncio.run(coro_func(*args, **kwargs))


def run_scan() -> ScanReport:
    """Sync entrypoint for /scan and tests.

    Two paths:
      - No running loop in this thread (APScheduler worker, CLI) → asyncio.run.
      - Running loop present (called from inside an async PTB handler) →
        offload to a worker thread; the coroutine is constructed there so we
        never produce a "coroutine was never awaited" warning.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_scan_async())

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_run_async_in_thread, run_scan_async)
        return future.result(timeout=300)


# --------------------------- helpers --------------------------------------

def _build_agent_input(
    *,
    stock,
    breakdown: dict,
    tech: dict,
    btc_price: float | None,
    btc_regime: str | None,
    btc_corr: float | None,
    macro_recent: list[dict],
    upcoming_events: list[dict],
    earnings_blackout: bool,
    insider_cluster: dict | None,
) -> AgentInput:
    from db.repos import sharia as sharia_repo
    sr = sharia_repo.latest_ratios(stock.symbol)
    return AgentInput(
        symbol=stock.symbol,
        sector=stock.sector,
        agent_set=stock.agent_set,
        sharia_status=stock.sharia_status,
        last_price=tech.get("last_close"),
        heuristic=breakdown,
        technical=tech,
        btc_price=btc_price,
        btc_regime=btc_regime,
        btc_corr_30d=btc_corr,
        btc_beta=stock.btc_beta,
        macro_recent=macro_recent,
        upcoming_events=upcoming_events,
        earnings_blackout=earnings_blackout,
        insider_cluster=insider_cluster,
        sharia_ratios=sr,
    )


def _persist_debate_signal(d: DebateResult) -> int | None:
    """Insert a row in `signals` for every debate (BUY, HOLD, PASS, VETOED)."""
    if d.vetoed:
        return signals_repo.insert_signal(
            symbol=d.symbol,
            decision="VETOED",
            trade_type=None,
            confidence=0.0,
            sharia_status=_sharia_snapshot(d),
            full_synthesis={"vetoed": True, "reason": d.veto_reason,
                            "round1": [_simplify(o) for o in d.round1]},
            veto_reason=d.veto_reason,
        )

    final = d.final
    if final is None:
        return None
    structured = final.structured or {}
    decision = (final.decision or "HOLD").upper()
    trade_type = structured.get("trade_type")
    confidence = float(final.confidence or 0.0)
    sharia_snap = _sharia_snapshot(d)
    sid = signals_repo.insert_signal(
        symbol=d.symbol,
        decision=decision,
        trade_type=trade_type,
        confidence=confidence,
        sharia_status=sharia_snap,
        full_synthesis={
            "decision": decision,
            "confidence": confidence,
            "rationale": final.rationale,
            "structured": structured.get("structured", {}),
            "round1": [_simplify(o) for o in d.round1],
            "round2": [_simplify(o) for o in d.round2],
        },
    )
    # Persist agent outputs linked to the signal
    for o in (d.round1 + d.round2):
        signals_repo.insert_agent_output(
            signal_id=sid, symbol=d.symbol,
            agent_name=o.agent_name, round_num=1 if o in d.round1 else 2,
            output=o.structured, confidence=o.confidence, decision=o.decision,
            input_tokens=o.usage.input_tokens, output_tokens=o.usage.output_tokens,
            cached_tokens=o.usage.cached_tokens, cost_usd=o.usage.cost_usd,
            latency_ms=o.usage.latency_ms,
        )
    if final:
        signals_repo.insert_agent_output(
            signal_id=sid, symbol=d.symbol,
            agent_name="synthesizer", round_num=3,
            output=structured, confidence=confidence, decision=decision,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            cached_tokens=final.usage.cached_tokens,
            cost_usd=final.usage.cost_usd, latency_ms=final.usage.latency_ms,
        )
    return sid


async def _maybe_push_signal_async(d: DebateResult, sid: int,
                                    btc_price: float | None) -> None:
    """Push a freshly-persisted signal to Telegram if all gates hold.

    Gates (all required):
      - decision == BUY                     (HOLD/PASS not actionable)
      - confidence >= MIN_CONFIDENCE_FOR_ALERT
      - sharia_status == HALAL              (defence-in-depth; vetoed signals
        already short-circuit in _persist_debate_signal)
      - runtime_config.alerts_paused != True
      - signals_repo.should_dedup(...) == False  (suppress same-symbol re-alerts
        inside DEDUP_WINDOW_HOURS unless confidence jumped DEDUP_CONFIDENCE_JUMP)

    Failures are logged and swallowed so a flaky Telegram never breaks a scan.
    """
    try:
        if d.vetoed or d.final is None:
            return

        decision = (d.final.decision or "HOLD").upper()
        if decision != "BUY":
            return

        confidence = float(d.final.confidence or 0.0)
        if confidence < MIN_CONFIDENCE_FOR_ALERT:
            return

        sharia_status = _sharia_snapshot(d)
        if sharia_status != "HALAL":
            return

        from db.repos import runtime_config
        if runtime_config.get_value("alerts_paused", False):
            log.info("scan alert suppressed (alerts paused)",
                     extra={"symbol": d.symbol})
            return

        if signals_repo.should_dedup(
            d.symbol,
            new_confidence=confidence,
            window_hours=DEDUP_WINDOW_HOURS,
            confidence_jump=DEDUP_CONFIDENCE_JUMP,
        ):
            log.info("scan alert suppressed (dedup)",
                     extra={"symbol": d.symbol, "signal_id": sid})
            return

        from telegram_bot.alerts import render_signal
        from telegram_bot.bot import send_text
        stock = stocks_repo.get(d.symbol)
        text = render_signal(
            d, btc_price=btc_price,
            sharia_verified_at=getattr(stock, "sharia_status_verified_at", None),
        )
        msg_id = await send_text(text)
        if msg_id is not None:
            signals_repo.mark_sent(sid, msg_id)
            log.info("signal pushed to telegram",
                     extra={"symbol": d.symbol, "signal_id": sid,
                            "telegram_msg_id": msg_id})
    except Exception as exc:
        log.warning("scan alert push failed",
                    extra={"symbol": getattr(d, "symbol", "?"),
                           "signal_id": sid, "err": str(exc)})


def _sharia_snapshot(d: DebateResult) -> str | None:
    sharia_out = next((o for o in d.round1 if o.agent_name == "sharia"), None)
    if sharia_out:
        return ((sharia_out.structured.get("structured") or {})
                .get("status") or sharia_out.decision)
    return None


def _simplify(o: AgentOutput) -> dict:
    return {
        "agent": o.agent_name,
        "decision": o.decision,
        "confidence": o.confidence,
        "rationale": o.rationale,
    }


def _quote_to_dict(q) -> dict:
    return {
        "speaker": q.speaker, "tier": q.tier, "venue": q.venue,
        "date": q.date, "quote_text": q.quote_text,
        "sentiment": q.sentiment, "source_url": q.source_url,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
