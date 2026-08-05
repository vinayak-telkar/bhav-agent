# Spec: DB schema (`schema.sql`)

**Owner:** Dev A · **Milestone:** M0/M1 · **Status:** done
**Depends on:** — (can be drafted in parallel with M0's ingest/LangGraph spikes — no shared
dependency, per `specs/README.md`)
**Consumed by:** ingest (01), all DAL modules (04, 05), all MCP tools (06)

## Purpose
Canonical SQLite DDL for all 7 tables referenced across the PRD and tech spec —
`symbols`, `daily_bars`, `fo_daily`, `market_days`, `users`, `watchlist`, `insights`.
Everything downstream (ingest writes, DAL reads, MCP tool output) is built against this.

## Interface / contract

```sql
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
```

## Acceptance criteria
- [ ] `schema.sql` applies cleanly to a fresh SQLite file with `PRAGMA foreign_keys = ON`
      enforced (a bad insert violating a FK should fail, not silently succeed).
- [ ] `watchlist` rejects a row with both `status='held'` and `size_bucket=NULL` at the
      application layer (CHECK constraints alone don't cross-validate this — confirmed in
      DAL, not schema, but flag the gap here so 04/05 don't miss it).
- [ ] The baseline window query in tech spec §3 is updated to add
      `WHERE corporate_action_flag = 0` (or an equivalent filter in the window frame) —
      **currently missing from the tech spec's example query; this schema introduces the
      column, so the query needs the filter added when 04 is built.**
- [ ] Seed script (a handful of symbols, ~25 days of synthetic `daily_bars` rows, one
      corporate-action-flagged row, one BE/T2T series change) loads cleanly and is reused
      by 06's MCP tool unit tests.
- [ ] Seed data includes at least one Category I (F&O-eligible) symbol and one Category II
      (non-F&O) symbol on the same trade dates, with `closing_price_method` set to
      `'cas_auction'` and `'vwap_30min'` respectively — confirms per-symbol category
      tagging works. (No test needed for a single symbol's value changing across its own
      history — that scenario doesn't arise, since this project's data starts fresh from
      the post-cutover regime; see spec's Changelog.)
- [ ] `insights` round-trips a full row (all NOT NULL fields populated, `evidence_json` a
      valid JSON string) via a manual insert + select.

## Out of scope
- Any DAL query logic (baseline windows, drill-down queries) — lives in 04/05, this spec
  is schema only.
- Historical/backtest-specific tables — the 6-month backtest reuses `daily_bars`/`fo_daily`
  as-is; no separate schema needed unless the backtest spec (later, M4) finds a gap.
- Migrations tooling — v1 is a single `schema.sql` applied once; no migration framework.

## Assumptions / open questions
- `basis` sign/unit convention (absolute vs %) — confirm against what ingest (01) actually
  parses out of the F&O bhavcopy; adjust the column comment once confirmed, not the type.
- Whether `daily_bars.series` changing mid-history (EQ → BE/T2T) needs a separate
  `series_history` table for cleaner querying, or whether reading it off `daily_bars`
  directly is sufficient for the MVP's "move to BE/T2T" signal — current call: sufficient,
  revisit only if the DAL query (04) turns out awkward.
- **Authoritative source for the Category I (F&O-eligible, CAS-applicable) list per day.**
  Working assumption: a symbol is Category I on a given `trade_date` if it has a
  corresponding `fo_daily` row that date — confirm this actually matches NSE's published
  Category I list (referenced in the implementation circular) rather than assuming
  "has F&O data" and "is CAS-eligible" are perfectly identical; the eligibility list can
  itself change over time independent of CAS. Resolve in the ingest spike (spec 01).

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-07-23 | Initial schema, 7 tables | Canonical DDL per tech spec §3, drafted in parallel with M0 |
| 2026-08-02 | Added `daily_bars.closing_price_method` | SEBI's Closing Auction Session (CAS) goes live 2026-08-03, changing how `close` is computed for F&O-eligible stocks only; baseline windows spanning the cutover need to be detectable, same category of problem as `corporate_action_flag` but market-wide, not per-symbol |
| 2026-08-02 | Simplified `closing_price_method` usage | Project restarts data collection fresh from the new CAS regime rather than reconciling pre/post-cutover data — the column now reflects per-symbol category membership (Category I vs II) rather than a per-symbol regime change to detect; no mixed-window DAL logic needed for v1 |
| 2026-08-05 | Added `idx_insights_unique_per_day` — `UNIQUE(user_id, symbol, trade_date)` on `insights` | User reported duplicate cards in Needs Attention. Root cause: `save_insight` was a plain `INSERT`, so re-running the digest for a day that already had an insight for a symbol (manual "Run digest now", or — how this was actually triggered — three back-to-back live verification runs this session, see specs/07's Changelog) appended a sibling row instead of replacing it; the real DB had 4-5 duplicate rows per symbol for one trade_date. Cleaned up the existing duplicates (kept the newest row per group; all were still `status='pending'`, so no resolution history was lost) and added this constraint so it can't recur — paired with `save_insight`'s new upsert behavior (spec 05's Changelog). |
