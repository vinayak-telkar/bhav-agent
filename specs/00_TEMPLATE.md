# Spec: <component/feature name>

**Owner:** <name> · **Milestone:** <M0–M5> · **Status:** not-started / in-progress / blocked / done
**Depends on:** <other specs that must be done/stable first>
**Consumed by:** <other specs/components that build against this one's contract>

> Timebox writing this to ~15–20 min. This is a contract, not a design doc. If a field
> doesn't apply, delete it — don't pad.

## Purpose
1–2 sentences. What this component does and why it exists, no more.

## Interface / contract
The load-bearing section — this is what lets someone else build against this without
reading your implementation. Function signatures, request/response shapes, MCP tool
schemas, or DB schema fragments, as literal as possible.

```
# e.g.
def get_delivery_trend(symbol: str, days: int = 20) -> dict:
    """Returns: {"delivery_pct_series": [...], "deliv_qty_trend": "...", "avg_trade_size_trend": "..."}"""
```

## Acceptance criteria
Testable bullets. If you can't write a test against a line, rewrite the line.
- [ ] ...
- [ ] ...

## Out of scope
Explicit non-goals for *this* spec — prevents scope creep and duplicate work with
adjacent specs.

## Assumptions / open questions
Anything you're guessing at that the owner should confirm before/while building.

## Changelog
| Date | Change | Why |
|---|---|---|
| YYYY-MM-DD | Initial spec | — |
