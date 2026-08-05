# Spec: DAL — watchlist/insights (`app/data/watchlist.py`, `app/data/insights.py`, `app/data/symbols.py`)

**Owner:** implementation team · **Milestone:** M1 · **Status:** done
**Depends on:** 03 (schema)
**Consumed by:** 06 (MCP tools `get_portfolio`/`get_wishlist`/`get_prior_insights`/
`save_insight`), 08 (FastAPI routes)

## Purpose
User-scoped read/write DAL: held/wishlist symbols with size buckets, and the digest
agent's cross-day insight memory. Also a small `symbols.py` for the autocomplete route
and ingest's reference-data upserts (not in the original spec table but required by
tech spec §7's `/symbols` route and §4's FK dependency).

## Interface / contract
```python
def get_portfolio(conn, user_id) -> list[dict]          # status='held'
def get_wishlist(conn, user_id) -> list[dict]            # status='wishlist'
def add_watch(conn, user_id, symbol, status, size_bucket=None) -> dict
def update_watch(conn, user_id, symbol, *, status=None, size_bucket=None) -> dict
def promote_to_held(conn, user_id, symbol, size_bucket) -> dict   # "I bought it"
def remove_watch(conn, user_id, symbol) -> None
# raises watchlist.ValidationError if status='held' with size_bucket=None,
# or status='wishlist' with a non-null size_bucket

def save_insight(conn, *, user_id, symbol, trade_date, signal_type, action,
                  confidence, evidence: dict, price_at_insight) -> int  # insight id
def get_prior_insights(conn, user_id, symbol, limit=5) -> list[dict]   # newest first
def get_insights_history(conn, user_id, symbol=None) -> list[dict]

def search_symbols(conn, query, limit=20) -> list[dict]   # symbol/name prefix+substring
def upsert_symbol(conn, *, symbol, name, isin, series, listing_date, last_updated) -> None
```

## Acceptance criteria
- [x] `add_watch`/`update_watch` reject `status='held'` with `size_bucket=None` and
      `status='wishlist'` with a non-null `size_bucket` — the cross-validation spec 03
      flagged as missing from CHECK constraints (`tests/test_dal.py`).
- [x] `promote_to_held` is a thin, obviously-named wrapper over `update_watch` — no
      separate code path to keep in sync.
- [x] `save_insight`/`get_prior_insights` round-trip `evidence` as a dict (JSON
      serialized/deserialized transparently — callers never see raw `evidence_json`).
- [x] New insights are always created with `status='pending'` — status transitions
      (strengthening/confirmed/expired) and `outcome_pct` resolution belong to the M4
      backtest/tracker job, out of scope for this iteration.

## Out of scope
- Insight status resolution / outcome_pct backfill (M4).
- Any ranking/prioritization logic across a user's portfolio — that's the digest graph's
  job (spec 07), not the DAL's.

## Assumptions / open questions
- `search_symbols` limit defaults to 20 — arbitrary but generous for an autocomplete
  dropdown; revisit if the real symbol universe's substring matches get noisy.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation | M1 — written just-in-time per TECH_SPEC.md §10 |
| 2026-08-05 | `save_insight` now upserts (`INSERT ... ON CONFLICT(user_id, symbol, trade_date) DO UPDATE`) instead of a plain `INSERT` | Needs Attention was showing duplicate cards for the same symbol — a digest re-run on a day that already had an insight for a symbol created a sibling row rather than replacing it (see specs/03's Changelog for the paired `UNIQUE` index and the real-DB cleanup this required). A same-day re-run now fully replaces the prior read: signal/action/confidence/narrative/evidence/price all update, and `status`/`outcome_pct`/`resolved_at` reset to fresh-insight defaults (`pending`/`NULL`/`NULL`) rather than carrying over stale resolution state from the row being replaced. |
