# Spec: FastAPI routes (`app/routes/*`, `app/main.py`)

**Owner:** implementation team · **Milestone:** M2/M3 · **Status:** done
**Depends on:** 04, 05 (DAL)
**Consumed by:** 09, 10 (frontend — manage screen + dashboard)

## Purpose
REST surface over the DAL for the React frontend, plus scheduler wiring for the daily
digest job. Single hardcoded demo user (`app/config.DEMO_USER_ID`), no auth (tech spec §7).

## Interface / contract
```
GET    /symbols?q=              -> list[{symbol, name, series}]                (empty q -> [])
GET    /watchlist?status=       -> list[{user_id, symbol, status, size_bucket, added_at}]
POST   /watch                   body {symbol, status, size_bucket?} -> the row      (201)
PATCH  /watch/{symbol}          body {status?, size_bucket?} -> the row             ("I bought it" = this)
DELETE /watch/{symbol}          -> 204
GET    /digest/today            -> {trade_date, insights: list[dict]}          (evidence pre-parsed to dict)
GET    /holdings                -> list[{...watchlist row, snapshot: dict|null}]
GET    /insights/history?symbol= -> list[dict]                                  (all dates, tracker feed)
POST   /digest/run              -> list[dict]   (NOT in original tech spec §7 — demo/testing
                                                   convenience: runs run_daily_digest()
                                                   synchronously instead of waiting for the
                                                   18:30 cron job. Needs a live GROQ_API_KEY.)
```
`POST /watch` and `PATCH /watch/{symbol}` return `400` with a message when
`watchlist.ValidationError` fires (held without size_bucket, or vice versa).

## Acceptance criteria
- [x] All routes tested against a seeded DB via FastAPI's `TestClient`
      (`tests/test_api.py`), except `POST /digest/run` — that one drives a real
      Groq/Cerebras call and needs a live API key not available in this build
      environment; verify it manually once `GROQ_API_KEY` is set (see TESTING.md).
- [x] `GET /watchlist` supports an optional `status` filter (`held`/`wishlist`) so the
      dashboard's holdings table and wishlist panel can each fetch only what they need.
- [x] CORS configured for the Vite dev server's default origin
      (`http://localhost:5173`) — the frontend and backend run as two separate dev
      processes.
- [x] `app.main`'s `lifespan` calls `db_module.init_db()` on startup (idempotent schema
      apply) and registers the daily digest as an APScheduler cron job (18:30, after
      NSE's EOD files are typically available) rather than requiring a separate process.

## Out of scope
- `POST /chat` — chat agent not built this iteration (spec 12).
- Any auth/session mechanism — v1 has exactly one demo user.
- Pagination on `/insights/history` — fine at demo data volumes; revisit if the tracker
  needs to show months of history.

## Assumptions / open questions
- The 18:30 cron time is a placeholder for "after NSE's EOD files are typically
  published" — not verified against actual publication timing on a live network
  (ingest access is itself blocked from this build environment, spec 01).

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation | M2/M3 — written just-in-time per TECH_SPEC.md §10 |
