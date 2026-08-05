# Spec: NSE bhavcopy ingest (`ingest/bhavcopy.py`)

**Owner:** implementation team · **Milestone:** M0 (gate) · **Status:** automated path blocked on live verification; manual-download fallback (`ingest/local_ingest.py`) built and tested end-to-end — see Changelog
**Depends on:** schema.sql (can be drafted in parallel, finalized together on day 1)
**Consumed by:** DAL (`app/data/bars.py`, `app/data/fo.py`), all downstream MCP tools

## Purpose
Download NSE's three end-of-day files for a given trade date, parse them, and load them
into `daily_bars` / `fo_daily`, idempotently.

## Interface / contract

```python
def ingest_date(trade_date: date) -> IngestResult:
    """
    Downloads + parses cash UDiFF bhavcopy, sec_bhavdata_full (delivery), and F&O
    bhavcopy for trade_date. Idempotent: safe to re-run for a date already ingested
    (upsert, not insert). Returns:
    """

@dataclass
class IngestResult:
    trade_date: date
    status: Literal["ok", "skipped_non_trading_day", "skipped_already_ingested", "failed"]
    symbols_loaded: int
    corporate_actions_flagged: list[str]   # symbols with suspected splits/bonuses
    error: str | None

def backfill(start: date, end: date) -> list[IngestResult]:
    """Calls ingest_date for each trading day in range, skipping non-trading days."""
```

Raw files archived to `data/raw/{trade_date}/` before parsing, regardless of parse outcome
— reprocessing from disk must be possible without re-hitting NSE.

**Manual-download fallback (`ingest/local_ingest.py`), added after confirming the
automated path is blocked from this build environment — see Changelog:**
```python
def nse_download_links(trade_date: date) -> list[dict]:
    """Links for the user to open in their own browser (direct_url + a hub_url
    fallback), one per file type."""

def find_local_files(downloads_dir: Path, trade_date: date) -> dict[str, FileMatch]:
    """Forgiving filename match per file type (case-insensitive, tolerates
    browser '(1)' duplicate suffixes) + a candidate list of other recent
    .csv/.zip files for manual disambiguation."""

def ingest_from_local_files(trade_date, cash_file, delivery_file=None, fo_file=None,
                             db_path=None) -> IngestResult:
    """Same parse+load logic as ingest_date() (both call the shared
    _process_and_load() helper in bhavcopy.py) — sources bytes from disk
    instead of a network request."""
```
Exposed three ways, all thin wrappers over the same functions (no duplicated logic):
an MCP tool (`ingest_local_bhavcopy`, mcp_server/server.py — makes this part of the
agent's tool surface), FastAPI routes (`GET /ingest/links`, `POST /ingest/check`,
`POST /ingest/run-local` — app/routes/ingest.py), and a frontend screen
(`frontend/src/pages/IngestScreen.tsx`) that walks the user through the whole flow.

## Acceptance criteria
- [ ] `ingest_date()` on one real recent trading day produces correct row counts in
      `daily_bars` and `fo_daily` for a manually spot-checked sample of 5 symbols.
- [ ] Re-running `ingest_date()` for the same date is a no-op on data (upsert, not
      duplicate rows) — verify via row count before/after.
- [ ] Calling `ingest_date()` on a known market holiday returns
      `status="skipped_non_trading_day"` without error.
- [ ] `backfill()` over a 2-week range that includes at least one holiday completes and
      correctly skips it.
- [ ] A synthetic/known split or bonus in the test window is flagged in
      `corporate_actions_flagged` (heuristic, not exact classification — see out of scope).
- [ ] Session warm-up (cookie acquisition) + retry/backoff survives at least one
      rate-limit response from NSE without crashing the whole run.
- [ ] **SEBI Closing Auction Session (CAS), live from 2026-08-03:** confirm whether NSE's
      cash UDiFF bhavcopy format reflects it (new columns, an explicit Category I/II or
      CAS-session flag) — check this on the very first real day the spike touches, since
      this project's ingest starts fresh from the new regime (no pre-cutover data is ever
      collected). Set `daily_bars.closing_price_method` (schema, spec 03) to
      `'cas_auction'` for Category I symbols, `'vwap_30min'` for Category II — every row
      gets one of these two values, not a date-dependent choice. Cross-check the working
      "Category I ≈ has an `fo_daily` row that day" assumption against whatever NSE
      actually publishes as the authoritative list, if that's available in the file or a
      companion circular.
- [ ] `fo_daily.basis` parsing accounts for the shifted derivatives settlement VWAP window
      (now 3:10–3:40 pm instead of ending 3:30 pm) if that window is used in computing it
      rather than taken directly as a published field.
- [x] **Manual-download fallback** (`ingest_from_local_files()`): loads a real cash-only
      file correctly (`deliv_pct`/`closing_price_method` correctly left as
      `None`/`'vwap_30min'` when delivery/F&O files aren't provided), loads cash+delivery
      together correctly, fails gracefully (not an exception) when the cash file is
      missing, and file-discovery tolerates case differences and browser `" (1)"`
      duplicate-download suffixes — `tests/test_local_ingest.py`,
      `tests/test_ingest_routes.py`, plus a full manual browser click-through of the
      frontend flow against the live backend.

## Out of scope
- Exact corporate-action classification (split ratio, bonus ratio) — flag-only.
- Price adjustment for corporate actions.
- Real-time/intraday data.
- The 6-month bulk backtest loader — separate spec, built after this one is proven on a
  single day (M0 exit criterion, not part of this spec).

## Assumptions / open questions
- ~~Exact current NSE URLs~~ — **resolved 2026-08-03:** all three URL patterns confirmed
  correct against live downloads (Changelog). What's still unresolved: whether a
  session-warm-up + browser-like headers pattern can ever get a *scripted* client past
  Akamai (evidence so far says no, regardless of network — Changelog), so the automated
  path (`ingest_date()`) should be treated as unlikely to work without further anti-bot
  work; the manual-download fallback (`ingest/local_ingest.py`) is the supported path.
- Corporate-action detection heuristic: overnight adjusted-close discontinuity beyond N%
  co-occurring with a volume anomaly. N to be tuned once real data is in hand.
- Whether NSE ships an explicit CAS/Category-I flag in the raw file, or whether it must be
  inferred (from F&O-eligibility presence, or a separately published list) — resolve on
  first real-file inspection, don't guess the file format ahead of looking at it.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-07-23 | Initial spec | M0 gate — de-risk before anything else is built |
| 2026-08-02 | Added CAS-related acceptance criteria | SEBI's Closing Auction Session goes live 2026-08-03, changing closing-price computation for F&O-eligible stocks; affects the very first real day this spike ingests |
| 2026-08-02 | `ingest_date()`/`backfill()` implemented against documented NSE UDiFF/sec_bhavdata_full URL+column formats; session warm-up, retry/backoff, corporate-action heuristic, closing_price_method assignment all in place | Live verification blocked in this build environment: a plain `curl` to nseindia.com from this sandbox returns HTTP 403 from Akamai bot-detection on the homepage itself, before cookies or parsing are even relevant. Parsing logic is proven correct against synthetic fixtures shaped like NSE's documented formats (`tests/test_ingest.py`), not against a live download. |
| 2026-08-03 | **All three URL patterns (`CASH_UDIFF_URL`, `DELIVERY_URL`, `FO_UDIFF_URL`) confirmed correct against live NSE downloads**, via the user manually clicking through the app's generated links in their own browser. `FO_UDIFF_URL` initially 404'd for the current date; turned out to be a publish-timing issue, not a wrong URL — NSE hadn't published that day's F&O file yet (it lagged behind cash/delivery, which were already out). Confirmed correct once tried against 2026-07-31 (a date whose files were already fully published). **Fix applied:** `IngestScreen.tsx` now defaults its date picker to the last *completed* trading day (before ~19:00 local time, defaults to the previous weekday; skips weekends) instead of blindly defaulting to "today," with an explanatory note in the UI. This removes the single biggest open risk this spec flagged (`# Assumptions / open questions`, NSE URL/access uncertainty) — the URLs are right; same-day publish lag was the actual issue. |
| 2026-08-03 | Re-tested from the user's actual home network (not this build sandbox) — still HTTP 403 from Akamai. **Revises the prior entry's hypothesis:** this is not primarily an IP/datacenter-reputation block, since the same 403 occurred from a residential IP; Akamai's bot-detection is more likely reacting to signals a bare HTTP client can't satisfy (TLS/JA3 fingerprint, missing JS-challenge execution, no real browser session) regardless of whose network the request comes from. Also tried routing the download through this session's browser-automation tool — external site navigation was denied outright at a permission/policy layer, before Akamai was even reached. **Built a manual-download fallback in response** (`ingest/local_ingest.py`, `app/routes/ingest.py`, `frontend/src/pages/IngestScreen.tsx`, `mcp_server/server.py`'s `ingest_local_bhavcopy` tool): the user downloads the three files via their own ordinary browser (which does pass Akamai, since it's a real browser session), and the app locates + parses + loads them — reusing `bhavcopy.py`'s exact parse logic via a new shared `_process_and_load()` helper, no duplication. Verified end-to-end in a real browser against the live FastAPI backend, including the error path (file not found) and the success path (a realistic fixture file, confirmed loaded correctly into SQLite). This fallback, not the automated network path, is the recommended way to get real NSE data into this app for now. `ingest/seed_data.py` remains the tested path every other component (DAL, MCP tools, digest agent, frontend) is built and tested against. |
| 2026-08-03 | **Real bug found and fixed: `_parse_delivery_file` silently returned zero delivery data for every symbol.** User ran the manual ingest flow with all three real files, confirmed selected in the UI, but every ingested symbol showed `deliv_pct=None`. Root cause: NSE's actual `sec_bhavdata_full` CSV uses `", "` (comma + space) as its field separator, not a bare `","`. `csv.DictReader` without `skipinitialspace=True` treats the leading space as part of every column name after the first (the real key is `" SERIES"`, not `"SERIES"`) — every `.get("SERIES", ...)`/`.get("DELIV_QTY", ...)`/`.get("DELIV_PER", ...)` lookup silently fell through to its default instead of raising, so every row was skipped and no error surfaced anywhere in the pipeline. Confirmed by inspecting the user's actual downloaded file directly. Fixed with `skipinitialspace=True` on all three `csv.DictReader` calls (harmless on cash/F&O, which use a clean `,` delimiter — also confirmed against real files, no issue there). Re-verified against the real delivery file afterward: 2,409 symbols parsed correctly. `tests/test_ingest.py`'s `DELIVERY_CSV` fixture updated to replicate the real comma-space format (a plain-comma fixture would have hidden this bug, and did — the original fixture didn't catch it). **Separately, a real database-integrity bug surfaced during the same debugging session:** `ingest/seed_data.py`'s synthetic demo symbols originally used real NSE tickers (RELIANCE, TCS, INFY, SMALLCAP, BEALERT). Once real ingest wrote to the same shared app database, upserts silently overwrote the synthetic RELIANCE row for the overlapping date with real market data, producing a fake ~59% single-day "drop" purely from the collision — which the digest agent would have flagged as a genuine signal. Fixed at the root: renamed all synthetic symbols to clearly-fake `DEMO*` tickers (`ingest/seed_data.py`'s docstring has the full list and reasoning), and — the more important fix — **synthetic seed data no longer runs against the app's real database at all.** `ensure_demo_user()` (`app/data/db.py`) now bootstraps just the demo user row on startup; real usage starts with an empty `symbols`/`daily_bars`/`watchlist` and expects ingest (this spec) to run first, then Manage to add real tickers — see TESTING.md's rewritten §1–§7. `seed_data.py` is now purely a pytest-fixture generator, run only against disposable `tmp_path` databases. |
