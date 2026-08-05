# Spec: Frontend — digest dashboard (`frontend/src/pages/Dashboard.tsx`)

**Owner:** implementation team · **Milestone:** M3 · **Status:** done
**Depends on:** 08 (FastAPI route contract)
**Consumed by:** —

## Purpose
Read-only dashboard: market-context strip, needs-attention cards, holdings table with
signal badges, wishlist panel, insight tracker (PRD §5).

## Interface / contract
Consumes `GET /digest/today`, `GET /holdings`, `GET /watchlist?status=wishlist`,
`GET /insights/history` (all read-only, no LLM call on page load per tech spec §7), plus
`POST /digest/run` — a demo/testing convenience, not in the original tech spec §7 route
list, added so the dashboard can be exercised without waiting for the 18:30 cron job.

`ActionBadge` (`frontend/src/components/ActionBadge.tsx`) color-codes the seven actions
into four tones (neutral/positive/warning/danger) by substring match on the action text.

## Acceptance criteria
- [x] Page-load data fetches are sequential (not `Promise.all`), guarded by a
      monotonically increasing `latestRequestId` ref so a slower, superseded fetch can't
      clobber state set by a newer one — this is a real race-condition fix, found because
      React StrictMode's dev-mode double-invocation of effects actually triggered it
      during manual browser testing (a stale error landed after a successful load already
      populated the page). Not a hypothetical; reproduced and fixed.
- [x] "Needs attention" filters to insights whose action isn't a "hold" — an empty state
      ("nothing crossed a threshold today") is rendered as a valid, valuable result per
      PRD §4, not as an empty/broken-looking table.
  - [x] Holdings table joins each holding with its most recent insight (by symbol) from
      `/insights/history`, not from `/digest/today` alone — so a signal badge still shows
      after today's digest window if the dashboard is viewed before a new run.
- [x] Verified end to end in a real browser against the live FastAPI backend and seeded
      DB — holdings, wishlist, and digest-today all confirmed rendering the correct
      numbers via screenshot.

## Out of scope
- Charting/sparklines for delivery% or price trend — table + numbers only for v1.
- `POST /digest/run`'s real Groq/Cerebras call was not exercised in this build
  environment (no API key) — the button and error-surfacing path are implemented and
  the error message explicitly tells the user what's missing; verify the happy path
  manually once a key is configured (see TESTING.md).

## Assumptions / open questions
- None outstanding.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation and manual browser verification | M3 — written just-in-time per TECH_SPEC.md §10; the request-race fix was discovered by actually running the app in a browser, not by code review |
