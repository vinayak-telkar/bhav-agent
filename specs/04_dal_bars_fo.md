# Spec: DAL — bars/fo (`app/data/bars.py`, `app/data/fo.py`)

**Owner:** implementation team · **Milestone:** M1 · **Status:** done
**Depends on:** 01 (ingest — schema-compatible rows), 03 (schema)
**Consumed by:** 06 (MCP tools `get_stock_snapshot`, `get_delivery_trend`, `get_fo_positioning`)

## Purpose
Read-side queries over `daily_bars`/`fo_daily`: today's snapshot + baseline comparison,
rolling delivery trend, and F&O buildup classification. All baseline windows exclude
`corporate_action_flag = 1` rows (spec 03's noted gap, fixed here).

## Interface / contract
```python
def get_stock_snapshot(conn, symbol: str) -> dict | None:
    """OHLC + prev_close + vwap + volume/deliv_pct + their 20-session baselines
    (ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) + turnover_rank_today.
    None if symbol has no daily_bars rows."""

def get_delivery_trend(conn, symbol: str, days: int = 20) -> dict | None:
    """Trailing `days` sessions: deliv_pct_series, deliv_qty_series,
    avg_trade_size_series (all oldest->newest) plus 'rising'/'falling'/'flat'/
    'insufficient_data' trend labels (first-half vs second-half average, >5% move)."""

def get_fo_positioning(conn, symbol: str) -> dict | None:
    """Latest fo_daily row + buildup_classification derived from the
    price-change/OI-change quadrant (long_buildup/short_covering/
    short_buildup/long_unwinding/insufficient_data). None if no F&O data
    (i.e. Category II / non-F&O-eligible symbol)."""
```

## Acceptance criteria
- [x] Baseline window queries filter `corporate_action_flag = 0`.
- [x] `get_fo_positioning` returns `None` (not an error) for a symbol with no `fo_daily`
      rows — this is how the digest graph tells Category I from Category II.
- [x] `_trend_label` degrades to `"insufficient_data"` under 4 data points rather than
      raising or dividing by zero.
- [x] Unit-tested against `ingest/seed_data.py`'s synthetic dataset (`tests/test_dal.py`).

## Out of scope
- Write paths — ingest owns all inserts into these tables.
- Market-wide scans (`scan_market`) — additive tool, not MVP.

## Assumptions / open questions
- `turnover_rank_today` ranks only against symbols with a non-null turnover value that
  day — fine for the demo's five-symbol universe; revisit ranking semantics once real
  NSE data (thousands of symbols) is loaded.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation | M1 — spec written just-in-time per TECH_SPEC.md §10, confirming the interface actually built rather than guessing ahead of the DB/seed data existing |
