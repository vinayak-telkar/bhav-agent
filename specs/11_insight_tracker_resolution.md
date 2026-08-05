# Spec: Insight status resolution (`agent/resolve_insights.py`)

**Owner:** implementation team · **Milestone:** M4 (partial — status resolution only, not
the 6-month backtest) · **Status:** in-progress
**Depends on:** 05 (insights DAL), 07 (digest graph — insights it creates are what this
resolves)
**Consumed by:** 08 (FastAPI `/digest/run`, `/digest/run-stream`), scheduler

## Purpose
Closes a real, user-noticed gap: every insight was created `pending` and stayed that way
forever — nothing ever compared a past call against what the price actually did
afterward. This is the (non-backtest) half of M4: a deterministic job that walks every
`pending`/`strengthening` insight, checks how the symbol's price has moved since
`price_at_insight`, and updates `status`/`outcome_pct` accordingly. Explicitly **not**
the 6-month historical backtest (PRD §6's evaluation section) — that needs bulk
historical NSE data this project has no practical way to acquire (NSE blocks scripted
downloads; the manual-download flow doesn't scale to ~380+ files). Deferred; flag for
whoever picks it up later, per specs/01's note on this same constraint.

**No LLM involved** — same "tools compute all numbers" philosophy as the digest's MCP
tools (tech spec §5): this is arithmetic over already-ingested `daily_bars`, not a
judgment call requiring a model.

## Interface / contract
```python
HORIZON_SESSIONS = 5        # PRD §4's own language: "Review; resolve over ~5 sessions"
CONFIRM_THRESHOLD_PCT = 3.0 # directional move needed to call it confirmed/expired
STRENGTHEN_THRESHOLD_PCT = 1.5  # smaller early-signal threshold, pre-horizon

SIGNAL_EXPECTED_DIRECTION: dict[str, Literal["up", "down", "flat"] | None] = {
    "genuine_accumulation": "up",
    "leveraged_rally": "down",             # thesis: leveraged/unsustainable -> pullback
    "quiet_distribution": "down",          # large holder exiting
    "short_buildup": "down",
    "capped_upside": "flat",
    "speculative_churn": "flat",           # noise, not signal -> expect no lasting move
    "liquidity_deterioration": "down",
    "positional_support_leaving": "down",
    "no_signal": None,                     # no directional call was made -> not evaluated
    "ungrounded_fallback": None,           # template fallback, no real read -> not evaluated
}

def resolve_insights(user_id: str, db_path: str | None = None) -> list[dict]:
    """Walks every pending/strengthening insight for user_id. For each:
    outcome_pct = (latest_close - price_at_insight) / price_at_insight * 100
    (always recomputed and persisted, even if status doesn't change this run —
    the tracker should show live in-progress movement, not just a final
    number; schema's 'NULL until resolved' comment refers to *before the
    first check*, not 'only on terminal status').
    sessions_elapsed = COUNT(daily_bars rows for symbol with trade_date >
    insight.trade_date). Skipped entirely if 0 (no new data yet).
    New status (see _classify): 'confirmed'/'expired' only set once
    sessions_elapsed >= HORIZON_SESSIONS; 'strengthening' can fire earlier on
    a clear early directional match; 'resolved_at' is set only on the
    confirmed/expired transition (terminal).
    Returns the list of insight dicts that were actually updated this run."""

def stream_resolve_insights(user_id: str, db_path: str | None = None) -> Iterator[str]:
    """Same resolution, yielding a human-readable progress line per insight
    checked (mirrors agent.digest_graph.stream_daily_digest's UX) — a plain
    sync generator, not a LangGraph stream, since there's no graph here."""
```

**Wiring:** `POST /digest/run` and `GET /digest/run-stream` (`app/routes/digest.py`) call
resolution *before* generating new insights — "check what happened to past calls, then
make new calls," the same order a real analyst would work in. Same order in the
scheduler (`app/main.py`). Resolution is not exposed as its own separate user-facing
action; it's a step inside the existing digest trigger, not a new one, since there's no
scenario where a user would want new insights without also refreshing old ones.

## Acceptance criteria
- [ ] A `pending` insight whose signal direction and threshold are confirmed by
      `HORIZON_SESSIONS`+ sessions of price data moves to `confirmed`, `outcome_pct` and
      `resolved_at` both set.
- [ ] A `pending` insight that moved the *wrong* direction (or stayed flat when a
      directional move was expected) past the horizon moves to `expired`.
- [ ] A `pending` insight with a clear early directional match but fewer than
      `HORIZON_SESSIONS` sessions elapsed moves to `strengthening`, `resolved_at` stays
      `NULL`.
- [ ] An insight with 0 sessions elapsed since `trade_date` (checked same day it was
      created) is left untouched — nothing to evaluate yet.
- [ ] `no_signal`/`ungrounded_fallback` insights move straight to `expired` once the
      horizon passes, without ever passing through `strengthening` (no directional claim
      was ever made to strengthen).
- [ ] Already-`confirmed`/`expired` insights are never touched again (only
      `pending`/`strengthening` are candidates).
- [ ] `outcome_pct` updates on every check, even when status doesn't change (so the
      tracker shows live movement toward/away from confirmation, not just a final number).

## Out of scope
- The 6-month historical backtest (PRD §6) — see Purpose. Separate spec, if the bulk
  historical data problem ever gets solved.
- Any LLM involvement in the classification — deliberately deterministic/arithmetic.
- Tuning `HORIZON_SESSIONS`/`CONFIRM_THRESHOLD_PCT`/`STRENGTHEN_THRESHOLD_PCT` against
  real outcome distributions — these are reasonable starting constants (matching
  `digest_graph.py`'s `_looks_off` philosophy: simple, tunable, not a statistical model),
  not validated against historical NSE data (that validation *is* the backtest, out of
  scope above).

## Assumptions / open questions
- `SIGNAL_EXPECTED_DIRECTION`'s up/down/flat mapping is a direct reading of PRD §4's
  signal→insight→action table, not independently validated — if a signal_type ever
  produces a confirmed/expired call that looks obviously wrong on inspection, revisit the
  mapping here first before assuming the resolution logic itself is broken.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-04 | Initial spec | User asked what's next after the core M0-M3 build; chose insight status resolution + chat agent (spec 12), explicitly deferring the 6-month backtest given the bulk-historical-data blocker already known from spec 01 |
