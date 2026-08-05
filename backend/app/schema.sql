-- Canonical DDL — see specs/03_db_schema.md (status: done as of 2026-08-02).
-- Do not diverge from the spec; if this file and the spec disagree, the spec's
-- Changelog is the source of truth for *why* something changed. Update both together.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Reference data ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS symbols (
    symbol          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    isin            TEXT,
    series          TEXT NOT NULL DEFAULT 'EQ',   -- current series; daily_bars.series is
                                                   -- the historical per-day record
    is_active       INTEGER NOT NULL DEFAULT 1,   -- 0 = delisted/suspended
    listing_date    TEXT,                         -- ISO 'YYYY-MM-DD'
    last_updated    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_days (
    trade_date      TEXT PRIMARY KEY,              -- ISO 'YYYY-MM-DD'
    is_trading_day  INTEGER NOT NULL,               -- 0/1 -- populated by ingest as it runs
    note            TEXT                            -- e.g. holiday name, nullable
);

-- ── Market data ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS daily_bars (
    symbol                  TEXT NOT NULL REFERENCES symbols(symbol),
    trade_date              TEXT NOT NULL,
    open                    REAL NOT NULL,
    high                    REAL NOT NULL,
    low                     REAL NOT NULL,
    close                   REAL NOT NULL,
    prev_close              REAL,
    vwap                    REAL,
    volume                  INTEGER NOT NULL,
    turnover                REAL,
    trades                  INTEGER,
    series                  TEXT NOT NULL,          -- series *that day* -- can change
                                                     -- (EQ -> BE/T2T; drives the "exit
                                                     -- liquidity deteriorating" signal)
    deliv_qty               INTEGER,
    deliv_pct               REAL,
    corporate_action_flag   INTEGER NOT NULL DEFAULT 0,  -- 1 = suppress this row from
                                                          -- baseline window queries
    closing_price_method    TEXT NOT NULL DEFAULT 'vwap_30min'
                             CHECK (closing_price_method IN ('vwap_30min', 'cas_auction')),
                             -- SEBI Closing Auction Session (CAS), effective 2026-08-03:
                             -- Category I (F&O-eligible) stocks are 'cas_auction' close;
                             -- Category II (non-F&O) stocks stay 'vwap_30min'. Since this
                             -- project's data collection starts fresh from the new regime
                             -- (no historical pre-cutover reconciliation), this column
                             -- reflects per-symbol category membership on a given day, not
                             -- a per-symbol regime change over time -- no single symbol's
                             -- own history ever crosses both values going forward.
                             -- Populated by ingest, not computed on read -- see spec 01.
    PRIMARY KEY (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trade_date);

CREATE TABLE IF NOT EXISTS fo_daily (
    symbol              TEXT NOT NULL REFERENCES symbols(symbol),  -- underlying; rolled
                                                                    -- up across contracts
                                                                    -- at ingest time
    trade_date          TEXT NOT NULL,
    fut_close           REAL,
    fut_oi              INTEGER,
    fut_oi_change       INTEGER,
    basis               REAL,              -- fut_close vs spot close -- confirm % vs
                                            -- absolute convention in ingest spec (01)
    pcr                 REAL,              -- put-call ratio by OI
    max_call_oi_strike  REAL,
    max_put_oi_strike   REAL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_fo_daily_date ON fo_daily(trade_date);

-- ── User data (privacy-minimal per PRD §3 — no PII, no broker login) ───────

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,   -- random UUID
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    symbol      TEXT NOT NULL REFERENCES symbols(symbol),
    status      TEXT NOT NULL CHECK (status IN ('held', 'wishlist')),
    size_bucket TEXT CHECK (size_bucket IN ('small', 'medium', 'large')),  -- NULL for
                                                                            -- wishlist rows
    added_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)   -- held or wished, never both; promotion is a one-line
                                     -- UPDATE (status: wishlist -> held)
);

CREATE TABLE IF NOT EXISTS insights (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL REFERENCES users(user_id),
    symbol            TEXT NOT NULL REFERENCES symbols(symbol),
    trade_date        TEXT NOT NULL,        -- digest run date this insight was generated on
    signal_type       TEXT NOT NULL,        -- one of the 8 signatures, PRD §4 table
    action             TEXT NOT NULL,       -- one of the 7 actions, PRD §4
    confidence        TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    narrative         TEXT NOT NULL,        -- the plain-English "why" (PRD §4: "never a
                                             -- bare label") -- was generated by the LLM
                                             -- but not persisted until this was noticed
                                             -- to be missing; see specs/07's Changelog
    evidence_json     TEXT NOT NULL,        -- raw evidence numbers the model cited
    status            TEXT NOT NULL CHECK (status IN
                          ('pending', 'strengthening', 'confirmed', 'expired')),
    price_at_insight  REAL NOT NULL,
    outcome_pct       REAL,                 -- NULL until resolved; powers tracker + backtest
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_insights_user_symbol_date
    ON insights(user_id, symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_insights_status ON insights(status);

-- One insight per symbol per trade_date: a digest run is meant to be that
-- day's read for a symbol, not an appended log entry, so re-running it
-- (manual "Run digest now", or the scheduled cron firing twice) must replace
-- the existing row via save_insight's upsert rather than create a sibling.
-- Without this, Needs Attention / Insight Tracker show duplicate cards for
-- the same symbol+day — confirmed live (2026-08-05) after repeated same-day
-- runs; see specs/07's Changelog.
CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_unique_per_day
    ON insights(user_id, symbol, trade_date);
