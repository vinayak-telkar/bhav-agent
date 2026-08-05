# Spec: Frontend — manage screen (`frontend/src/pages/ManageScreen.tsx`)

**Owner:** implementation team · **Milestone:** M3 · **Status:** done
**Depends on:** 08 (FastAPI route contract)
**Consumed by:** —

## Purpose
Add/remove symbols, set size bucket, symbol autocomplete, "I bought it" wishlist->held
promotion (PRD §5's first-run onboarding + ongoing management surface).

## Interface / contract
Consumes `frontend/src/api.ts`'s `searchSymbols`, `getWatchlist`, `addWatch`,
`updateWatch`, `removeWatch` — see spec 08 for the underlying route shapes.
`frontend/src/types.ts` mirrors the backend's response shapes; keep both in sync if the
route contract changes.

Promotion uses an inline size-bucket picker (select + Confirm/Cancel) rendered in place
of the "I bought it" button, **not `window.prompt()`** — a blocking native dialog can't
be styled, blocks the main thread, and doesn't work in embedded/automated contexts. This
was an explicit correction made while testing in the browser, not the original design.

## Acceptance criteria
- [x] Symbol autocomplete debounced (250ms) against `GET /symbols?q=`.
- [x] Adding a symbol as "held" requires a size bucket in the UI (mirrors the DAL's
      `ValidationError`); adding as "wishlist" omits the bucket selector entirely.
- [x] Promoting a wishlist item shows an inline bucket picker, not a blocking dialog.
- [x] Verified end-to-end in a real browser against the live FastAPI backend: search ->
      select -> add -> bucket update via re-add (upsert) -> promote -> remove, all
      confirmed via screenshot, not just a green test suite.
- [x] All interactive elements have explicit `color` (see `index.css`'s
      `color-scheme: light` note) — a real bug caught in manual testing: buttons with no
      explicit color inherited a dark-mode UA default (white text), rendering invisible
      against this app's white/transparent button backgrounds.

## Out of scope
- Any styling framework/design system — plain CSS (`App.css`), kept intentionally simple.
- Client-side form validation beyond what the backend already enforces (single source of
  truth for "held requires size_bucket" stays the DAL).

## Assumptions / open questions
- None outstanding.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation and manual browser verification | M3 — written just-in-time per TECH_SPEC.md §10; the `window.prompt()` -> inline-picker change and the invisible-button-text fix were both found by actually testing in a browser, not by reading the code |
