# XYZStocksUS

Premium multi-agent stock signals bot for US markets with full Sharia compliance integration. Designed to run 24/7 on Railway, with Telegram as the primary interface and a mobile-friendly Flask dashboard.

This is **separate** from the user's crypto bot (XYZTradingAE). Phase 1 ships signals only — paper trading is deferred to Phase 2.

---

## Project overview

| Concern | Stack |
| --- | --- |
| Worker (scans, scheduler, sharia jobs) | Python 3.11 + APScheduler + SQLite (WAL) |
| Web (dashboard + telegram polling) | Flask + gunicorn + python-telegram-bot v20 |
| LLM | Anthropic SDK with prompt caching (Haiku 4.5 + Sonnet 4.6) |
| Data | yfinance, Coinbase, SEC EDGAR, Federal Reserve RSS, mempool.space, OpenInsider RSS |
| Sharia | AAOIFI Standard 21 — implemented in Python, never produced by an LLM |
| Persistence | SQLite at `/data/xyzstocksus.db` (Railway volume) |
| Process supervisor | supervisord (web + worker in a single container) |

The repository follows a layered architecture:

```
config/        canonical settings, watchlist, agent sets, thresholds
data/          external API clients (yfinance, BTC, macro, SEC, OpenInsider…)
indicators/    pure-Python RSI/MACD/EMA/ATR + 0-100 heuristic scorer
sharia/        AAOIFI screen, ratios, verifier, monitor, purification, reporter
llm/           Anthropic client + prompt caching + Haiku pre-screen + prompts/
agents/        8 specialised agents + debate orchestrator
core/          cache, cost tracker, budget guard, scheduler, market calendar,
               top-level scan orchestrator
db/            schema, migrations, lean repos per concern
telegram_bot/  alert formatter, PTB application, command handlers, confirm flow
dashboard/     Flask app, SSE broadcaster, JSON API, mobile templates, static
workers/       gunicorn entrypoint (web.py) + scheduler entrypoint (scanner.py)
```

---

## Multi-agent architecture

### The 8 agents

| # | Agent | Module | Mandatory in |
| --- | --- | --- | --- |
| 1 | Technical Analyst 🔧 | `agents/technical_analyst.py` | every set |
| 2 | BTC Macro Analyst ₿ | `agents/btc_macro_analyst.py` | btc_full only |
| 3 | Fundamentals Analyst 📊 | `agents/fundamentals_analyst.py` | btc_full + standard |
| 4 | Risk Manager ⚠️ | `agents/risk_manager.py` | every set |
| 5 | Devil's Advocate 😈 | `agents/devils_advocate.py` | btc_full + standard |
| 6 | Macro Voice Analyst 🎤 | `agents/macro_voice.py` | every set |
| 7 | Sharia Compliance Officer 🕌 | `agents/sharia_officer.py` | every set (VETO power) |
| 8 | Synthesizer 🎯 | `agents/synthesizer.py` | every set |

### Agent sets (`config/agent_sets.py`)

| Set | Members | Used for |
| --- | --- | --- |
| `btc_full` | all 8 | BTC-correlated stocks (MSTR, miners, COIN, TSLA, NVDA, …) |
| `standard` | 7 (no BTC Macro) | regular equities |
| `lean` | 5 (Technical + Risk + Macro Voice + Sharia + Synthesizer) | ETFs (HLAL, SPUS, SPSK) |

The set per ticker is resolved from sector by `config.agent_sets.resolve_set_for_sector()`.

### Debate flow (`agents/debate.py`)

```
HARAM short-circuit ─→ deterministic Sharia veto, zero LLM cost, return.
budget guard check  ─→ if blocked, return without calling Sonnet.

Round 1 (asyncio.gather) — every set member except Synthesizer runs in
parallel, with its system prompt cached via Anthropic prompt caching.

Sharia post-R1 veto check — if Sharia officer decision == VETO or
structured.status == HARAM, R2 + R3 are skipped (saves tokens).

Round 2 — cross-critique (sequential). Fires when peak non-Sharia
confidence is in [0.60, 0.70] (refined band, ~30% of cases) or when
forced by /analyze.

Round 3 — Synthesizer reads R2 (or R1 if R2 skipped) + structured data.
Hard rules in its system prompt:
  • Sharia VETO or status == HARAM → final = PASS
  • Risk grade D → final = PASS
Outputs entry zone, stop, three TPs, R:R, final confidence.
```

### Two-tier LLM + caching

Heuristic scoring is pure Python ($0). Then for each scan:

1. **Top 15** by heuristic + insider-cluster auto-elevations (`core.orchestrator`)
2. **Single Haiku call** decides the dynamic 2–4 deep-analysis survivors (`llm/prescreen_haiku.py`)
3. **Sonnet** runs the full debate per survivor with cached system prompts

Prompt caching: every agent's system prompt block is marked `cache_control: ephemeral`. Across the 6+ parallel R1 calls, the system content is read from cache after the first call (~90% input-token reduction on cached content).

### Insider Cluster Detector

Implemented in `data/openinsider.py::detect_clusters` and integrated into `core/orchestrator.py::run_scan_async`. A symbol qualifies when:

- ≥ 3 unique insiders bought it within 14 days, AND
- at least one of them holds CFO / CEO / President / COO

Qualifying symbols on the watchlist are auto-elevated into the Haiku pre-screen pool regardless of heuristic score.

### BTC Dump Protection

`data/btc_feed.is_dump(drop_pct=0.05, window_min=60)` reads the `btc_snapshots` table (fed by the 1-min `btc_ping` job) and returns True when BTC has dropped ≥5% over the last 60 minutes. The orchestrator passes `skip_btc_full=True` into `run_debate_async()`, which drops the BTC Macro agent from any btc_full set for the duration of the dump.

---

## Sharia compliance system

### Standard

AAOIFI Shari'ah Standard No. 21 — three classifications (HALAL/MIXED/HARAM) and three ratio caps. Constants live in `config/thresholds.py` and are re-exported by `sharia/aaoifi.py`.

| Ratio | Cap |
| --- | --- |
| Interest-bearing debt / market cap | < 30% |
| Cash + interest-bearing securities / market cap | < 30% |
| Impermissible income / total revenue | < 5% |

Per-ratio alert tiers:

| Tier | Range |
| --- | --- |
| 🟢 GREEN | ratio < 25% |
| 🟡 YELLOW | 25% ≤ ratio < 30% |
| 🟠 ORANGE | 30% ≤ ratio < 33% |
| 🔴 RED | ratio ≥ 33% (clear breach → HARAM) |

### Pipeline

1. **Business activity screen** (`sharia/business_screen.py`) — hard-exclusion list (`config/excluded_stocks.py`: 17 banks/insurance/BTC futures ETFs/broad ETFs) + SIC-code blocklist + industry-name keyword screen.
2. **Ratio extraction** (`sharia/ratios.py`) — pulls from yfinance `Ticker.info` first, then SEC EDGAR `companyfacts` XBRL JSON (more accurate). Sums across debt aliases, picks the most recent end-date per concept.
3. **Status derivation** (`config/thresholds.py::derive_status`) — classifies each ratio into GREEN/YELLOW/ORANGE/RED, returns a `TierBreakdown`. Any RED → HARAM. Any ORANGE → MIXED.
4. **Drift radar** (`config/thresholds.py::is_drift_warning`) — fires when the last 4 quarters' debt slope is rising at ≥2pp/quarter AND the current ratio is within 3pp of the 30% breach.
5. **Verifier** (`sharia/verifier.py`) — orchestrates 1-4 + persists to `financial_ratios_history` and `stocks_metadata`.

The LLM **never** produces a ratio or a status — the Sharia Officer agent only renders explanations from numbers it received as structured input.

### Monitoring schedule

| Job | Schedule | Module |
| --- | --- | --- |
| Daily 10-Q sweep | 09:00 Asia/Dubai | `sharia/monitor.py::run_daily_check` |
| Weekly full scan | Saturday 10:00 Asia/Dubai | `sharia/monitor.py::run_weekly_full_scan` |
| Drift radar | runs inside both jobs | same module |

The daily job only checks symbols with OPEN positions in `user_positions` and skips when the latest 10-Q `filing_date` is unchanged from the last persisted ratios row. The weekly job re-verifies the full enabled watchlist. Both emit `STATUS_CHANGE`, `TIER_CHANGE`, and `DRIFT_WARN` rows into `compliance_alerts`. Drift alerts are de-duped per filing.

### Dividend purification

`sharia/purification.py` implements the AAOIFI dividend purification formula (`impermissible_ratio × dividend_per_share × quantity`), surfaced in `/sharia <SYMBOL>` and the weekly compliance report.

---

## Telegram commands

Two-way control. Destructive commands route through an inline-button Confirm/Cancel flow with a 60-second TTL (`telegram_bot/confirm.py`).

| Command | Description |
| --- | --- |
| `/start`, `/help` | welcome + full command menu |
| `/status` | last scan, market status, BTC, today/month spend |
| `/watch` | watchlist grouped by sector with Arabic Sharia badges + heuristic scores |
| `/btc` | BTC price + regime + SMA20/SMA50 |
| `/macro` | recent Powell / Fed / Trump quotes (with hawkish/dovish icons) |
| `/analyze SYMBOL` | full multi-agent debate (R1 + R2 + R3) |
| `/quick SYMBOL` | faster analysis (R1 + R3) |
| `/agents SYMBOL` | last debate broken down per agent |
| `/signals` | last 10 signals |
| `/sharia SYMBOL` | full Sharia status report (ratios, tiers, recent alerts) |
| `/compliance` | weekly compliance summary |
| `/buy SYMBOL @ PRICE × QTY` | record a position for compliance monitoring |
| `/sell SYMBOL` | close tracked positions for a symbol *(confirm)* |
| `/positions` | list OPEN positions with Sharia entry status |
| `/scan` | trigger a manual scan |
| `/cost` | API spend today + this month, per-agent breakdown |
| `/pause`, `/resume` | pause/resume Telegram alerts |
| `/disable SYMBOL` | exclude from scans *(confirm)* |
| `/enable SYMBOL` | re-include in scans *(confirm)* |
| `/threshold N` | change min-confidence-for-alert *(confirm)* |

Sharia status badges in alerts use English + Arabic: `🟢 HALAL (شرعي)`, `🟡 MIXED (مختلط)`, `🔴 HARAM (غير شرعي)`.

---

## Deployment guide (Railway)

1. **Create a Railway project** named `xyzstocksus` and connect this repo.
2. **Attach a persistent volume** mounted at `/data` (this stores the SQLite DB, file cache, jobstore, and logs).
3. **Set environment variables** (see `.env.example`):
   - `ANTHROPIC_API_KEY` — required for any LLM call
   - `TELEGRAM_BOT_TOKEN` — required for Telegram alerts + commands
   - `TELEGRAM_CHAT_ID` — destination chat (default `8588842240`)
   - `TZ=Asia/Dubai`
   - `DATA_DIR=/data`
   - `SEC_USER_AGENT="XYZStocksUS your-email@example.com"` (SEC requires a contact in UA)
   - Optionally `DAILY_SOFT_USD=2.50`, `DAILY_HARD_USD=5.00`, `MONTHLY_HARD_USD=80.00`, `MONTHLY_WARN_PCT=0.75`
4. **Push**. Railway builds via Nixpacks and runs `supervisord -c supervisord.conf -n` (from `Procfile`), which boots:
   - `web` — gunicorn serving `workers.web:app` (dashboard + Telegram polling thread)
   - `worker` — `python -m workers.scanner` (APScheduler with SQLite jobstore at `/data/jobstore.sqlite`)
5. **Health check**: `GET /health` returns `{"status": "ok"}`. Railway's `healthcheckPath` is set in `railway.toml`.

### Local quickstart

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate      # macOS/Linux
pip install -r requirements.txt
copy .env.example .env            # edit ANTHROPIC + TELEGRAM
gunicorn -b 0.0.0.0:8080 workers.web:app   # web in one terminal
python -m workers.scanner                  # worker in another
```

---

## Cost monitoring

- All LLM calls go through `llm/client.py::complete`, which writes a row into `api_costs` via `core/cost_tracker.record_call`.
- `core/budget_guard.py` enforces the budget hierarchy:

| Gate | Threshold | Effect |
| --- | --- | --- |
| Daily soft | $2.50 | log + Telegram alert; calls still proceed |
| Daily hard | $5.00 | block Sonnet calls; only Haiku pre-screen + `/quick` allowed |
| Monthly warn | 75% of $80 | auto-flip `runtime_config.quick_only=True` |
| Monthly hard | $80 | block all LLM calls |
| Daily deep cap | 30 Sonnet calls | block Sonnet for the rest of the day |

- `/cost` Telegram command shows today + month spend and a per-agent breakdown.
- Dashboard footer shows today's spend; `/api/cost` exposes the full state.

---

## Troubleshooting

### "No signals are appearing"
1. Check `/health` is OK on Railway.
2. Check the worker logs for `"scan start"` / `"scan done"` lines (one per scheduled slot).
3. Make sure today is a US trading day (`core/market_calendar.is_trading_day`).
4. Run `/scan` manually — the response shows `candidates → prescreen → deep → signals` numbers.
5. If `prescreen=0`, the Haiku call may have failed. Check `api_costs` for `prescreen` rows today.

### "Telegram alerts stopped"
1. `/cost` — if monthly spend > $60 (75%), the bot auto-flipped to `quick_only` mode (only `/quick` works). `/resume` clears it.
2. Check `runtime_config.alerts_paused` — `/resume` resets it.
3. Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set on Railway.

### "Sharia statuses are all PENDING"
The first weekly Sharia scan (Saturday 10:00 Dubai) verifies the full watchlist. To force it earlier, run the daily monitor manually or wait for the first weekly cron. Individual symbols update via `/analyze` (which currently doesn't write ratios — only the monitor does).

### "BTC Macro agent is missing from a btc_full debate"
That's intentional — it means BTC dropped ≥5% in the last 60 minutes and the dump-protection skip is active. Resumes automatically after a 60-min cooldown. The scheduled `btc_ping` job runs every minute and feeds the detector.

### "yfinance is throttling / breaking"
The price layer has a 5/sec rate limiter and 3-attempt exponential backoff. Single failures are logged and skipped; the scan continues with the data it does have. Watch `data.prices` warning log lines.

### "I want to override a stock's Sharia status manually"
Set it directly in SQLite:
```sql
UPDATE stocks_metadata SET sharia_status = 'HALAL', sharia_status_verified_at = datetime('now') WHERE symbol = 'XYZ';
```
The next `/sharia XYZ` and the dashboard will reflect it. The next scheduled monitor run may overwrite it if the financial data still says otherwise.

### "I want to add a new ticker to the watchlist"
Edit `config/watchlist.py`, push. On boot, `db/migrate.py` seeds new symbols (existing rows are never overwritten). If the seed gives wrong agent_set or btc_beta, edit the row directly in SQLite — the migration won't touch it again.

---

## Architecture decisions worth knowing

- **No paper trading** — Phase 1 explicitly. We just emit signals. Phase 2 will add a paper engine.
- **No Reddit data** — explicit user decision; relying on official Fed RSS, FOMC HTML, Trump RSS, and SEC.
- **Sharia ratios live in Python, not the LLM** — single source of truth, auditable. The Sharia officer agent only renders.
- **Deterministic Sharia veto fast-path** — `agents/sharia_officer.py` skips the LLM entirely when input is HARAM, saving tokens on the most common veto.
- **Caching strategy** — every agent's system prompt is cached. Across 6+ parallel R1 calls, only the first pays the full input price.
- **Lazy heavy imports** — yfinance, requests, feedparser, bs4, anthropic SDK are all imported inside functions so module imports stay clean and cold-start is fast.
- **No paper-position math in signal output** — entry/stop/TPs come from the synthesizer's structured payload, derived from technical.atr_14 + last_price + risk's stop_atr_multiple. They are recommendations, not orders.

---

## Phase 2 backlog (not in this codebase)

- Paper trading engine
- Learned agent weights from track record
- Powell tone fingerprint (embedding-based hawkish/dovish score)
- MSTR–BTC decoupling detector (premium-to-NAV signal)
- Auto-generated Arabic compliance PDF for Sharia advisor
- Backtesting harness over historical scans
- More macro voices: Yellen, Buffett 13F, earnings transcripts
- Multi-user support (currently single chat_id)
- Prometheus metrics + Grafana
- Alembic migrations (currently file-driven idempotent SQL)

---

## Test layout

```
tests/test_health.py            commit 1  /health
tests/test_config.py            commit 2  watchlist, exclusions, thresholds, drift
tests/test_db.py                commit 3  migrations, repos
tests/test_data.py              commit 4  cache, sentiment, insider clusters, BTC dump
tests/test_indicators.py        commit 5  RSI/MACD/EMA/ATR, scoring
tests/test_sharia_engine.py     commit 6  business screen, ratios, verifier, drift
tests/test_sharia_monitor.py    commit 7  daily/weekly monitor, alerts, reporter
tests/test_llm_budget.py        commit 8  pricing, JSON parser, budget gates
tests/test_agents_1_4.py        commit 9  agent base + analysts
tests/test_agents_5_8.py        commit 10 devil/macro/sharia/synth (incl. veto fast-path)
tests/test_debate.py            commit 11 R1 parallel, vetoes, R2 band, R3 synth
tests/test_market_calendar.py   commit 12 NYSE hours/holidays/early close
tests/test_telegram_alerts.py   commit 13 alert formatter
tests/test_telegram_handlers.py commit 14 confirm flow + position parser
tests/test_dashboard.py         commit 15 routes + SSE
```

Run with `pytest -q tests/`.

---

## License

Private — internal project. Not for redistribution.
