-- XYZStocksUS schema (Phase 1).
-- Loaded by db.migrate; do not run directly. Idempotent thanks to IF NOT EXISTS.
-- Each table mirrors the spec in the project plan; Sharia tables sit at the bottom.

-- =========================================================================
-- Migrations bookkeeping
-- =========================================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================================
-- Watchlist + per-symbol state
-- =========================================================================
CREATE TABLE IF NOT EXISTS stocks_metadata (
    symbol TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    btc_beta REAL NOT NULL DEFAULT 0,
    agent_set TEXT NOT NULL,                          -- btc_full | standard | lean
    enabled INTEGER NOT NULL DEFAULT 1,
    sharia_status TEXT NOT NULL DEFAULT 'PENDING',    -- HALAL | MIXED | HARAM | PENDING
    sharia_status_verified_at TEXT,
    expected_status TEXT,                             -- seed hint from watchlist
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_stocks_sharia ON stocks_metadata(sharia_status);
CREATE INDEX IF NOT EXISTS idx_stocks_enabled ON stocks_metadata(enabled);

-- =========================================================================
-- Heuristic scoring (no LLM, $0 per row)
-- =========================================================================
CREATE TABLE IF NOT EXISTS heuristic_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    rsi REAL,
    macd REAL,
    macd_signal REAL,
    volume_ratio REAL,
    btc_corr_30d REAL,
    score REAL NOT NULL,
    raw_json TEXT,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_heuristic_symbol_time ON heuristic_scores(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_heuristic_time ON heuristic_scores(timestamp);

-- =========================================================================
-- Haiku pre-screen verdicts
-- =========================================================================
CREATE TABLE IF NOT EXISTS prescreen_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    haiku_verdict INTEGER NOT NULL,                   -- 0/1
    haiku_reasoning TEXT,
    deep_analyze INTEGER NOT NULL DEFAULT 0,          -- final yes/no after caps
    cost_usd REAL,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_prescreen_symbol_time ON prescreen_results(symbol, timestamp);

-- =========================================================================
-- Multi-agent debate outputs
-- =========================================================================
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,                           -- BUY | HOLD | PASS | VETOED
    trade_type TEXT,                                  -- SCALP | SWING | LONG | NULL
    confidence REAL NOT NULL,
    sharia_status TEXT,                               -- snapshot at signal time
    full_synthesis_json TEXT,
    sent_to_telegram INTEGER NOT NULL DEFAULT 0,
    telegram_msg_id INTEGER,
    veto_reason TEXT,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);

CREATE TABLE IF NOT EXISTS agent_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    round_num INTEGER NOT NULL,                       -- 1 | 2 | 3
    output_json TEXT NOT NULL,
    confidence REAL,
    decision TEXT,                                    -- BUY | HOLD | PASS | VETO
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    FOREIGN KEY (signal_id) REFERENCES signals(id),
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_signal ON agent_outputs(signal_id);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_symbol_time ON agent_outputs(symbol, timestamp);

-- =========================================================================
-- BTC + macro context
-- =========================================================================
CREATE TABLE IF NOT EXISTS btc_snapshots (
    timestamp TEXT PRIMARY KEY,
    price REAL NOT NULL,
    regime TEXT,                                      -- BULL | BEAR | NEUTRAL
    source TEXT NOT NULL DEFAULT 'coinbase'
);

CREATE TABLE IF NOT EXISTS macro_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker TEXT NOT NULL,                            -- Powell | FOMC | Trump | ...
    tier INTEGER NOT NULL DEFAULT 1,                  -- 1 = highest impact
    venue TEXT,
    date TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    sentiment TEXT,                                   -- HAWKISH | DOVISH | NEUTRAL
    source_url TEXT,
    UNIQUE (speaker, date, source_url)
);
CREATE INDEX IF NOT EXISTS idx_macro_quotes_date ON macro_quotes(date);

CREATE TABLE IF NOT EXISTS macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,                         -- FOMC | CPI | NFP | EARNINGS | ...
    description TEXT,
    expected_impact TEXT,                             -- HIGH | MEDIUM | LOW
    UNIQUE (date, event_type, description)
);
CREATE INDEX IF NOT EXISTS idx_macro_events_date ON macro_events(date);

-- =========================================================================
-- Cost tracking + runtime config + command audit
-- =========================================================================
CREATE TABLE IF NOT EXISTS api_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model TEXT NOT NULL,                              -- haiku | sonnet
    agent TEXT,                                       -- 'prescreen' | agent name | NULL
    symbol TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_costs_time ON api_costs(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_costs_model ON api_costs(model, timestamp);

CREATE TABLE IF NOT EXISTS runtime_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS command_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    chat_id TEXT,
    command TEXT NOT NULL,
    args TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_command_log_time ON command_log(timestamp);

-- =========================================================================
-- SHARIA TABLES — AAOIFI compliance state
-- =========================================================================
CREATE TABLE IF NOT EXISTS financial_ratios_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    filing_date TEXT,
    filing_type TEXT,                                 -- 10-Q | 10-K | 8-K | derived
    market_cap REAL,
    total_debt REAL,
    interest_bearing_debt REAL,
    cash_and_securities REAL,
    total_revenue REAL,
    impermissible_revenue REAL,
    debt_ratio REAL,
    cash_ratio REAL,
    impermissible_ratio REAL,
    sharia_status TEXT,
    risk_tier TEXT,                                   -- worst-case tier across ratios
    notes TEXT,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_ratios_symbol_filing ON financial_ratios_history(symbol, filing_date);
CREATE INDEX IF NOT EXISTS idx_ratios_symbol_fetched ON financial_ratios_history(symbol, fetched_at);

CREATE TABLE IF NOT EXISTS compliance_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL,                         -- TIER_CHANGE | DRIFT_WARN | NEW_FILING | STATUS_CHANGE
    old_value TEXT,
    new_value TEXT,
    severity TEXT NOT NULL,                           -- INFO | WARN | CRITICAL
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    telegram_msg_id INTEGER,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_compliance_symbol_time ON compliance_alerts(symbol, sent_at);
CREATE INDEX IF NOT EXISTS idx_compliance_type ON compliance_alerts(alert_type, sent_at);

CREATE TABLE IF NOT EXISTS user_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    sharia_status_at_entry TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',              -- OPEN | CLOSED
    closed_date TEXT,
    closed_price REAL,
    notes TEXT,
    FOREIGN KEY (symbol) REFERENCES stocks_metadata(symbol)
);
CREATE INDEX IF NOT EXISTS idx_positions_symbol_status ON user_positions(symbol, status);
