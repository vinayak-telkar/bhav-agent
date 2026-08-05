# Spec: MCP server + 5 core tools (`mcp_server/server.py`)

**Owner:** implementation team · **Milestone:** M1 · **Status:** done
**Depends on:** 04, 05 (DAL)
**Consumed by:** 07 (digest graph), 12 (chat graph, out of scope this iteration)

## Purpose
The only tool surface for both agents (tech spec §5). FastMCP, stdio transport. Thin
wrappers over `app/data/*` — no query logic lives here.

## Interface / contract
```python
get_stock_snapshot(symbol: str) -> dict            # {"error": ...} if unknown symbol
get_delivery_trend(symbol: str, days: int = 20) -> dict
get_fo_positioning(symbol: str) -> dict             # {"error": ...} if not F&O-eligible
get_portfolio(user_id: str) -> list[dict]
get_wishlist(user_id: str) -> list[dict]
get_prior_insights(user_id: str, symbol: str, limit: int = 5) -> list[dict]
save_insight(user_id, symbol, trade_date, signal_type, action, confidence,
             evidence: dict, price_at_insight: float) -> dict   # {"id":, "status": "saved"}
```
Transport config for both agents:
```python
{"bhav": {"transport": "stdio", "command": sys.executable,
          "args": [".../mcp_server/server.py"], "env": {...}, "cwd": ".../backend"}}
```
`command` must be a full interpreter path, not `"python"` (spec 02 finding).

## Acceptance criteria
- [x] All 7 tools (5 "core" per tech spec §5, counting portfolio/wishlist and
      prior-insights/save-insight as pairs) discovered via a real
      `MultiServerMCPClient` round trip against a seeded DB, not just unit-tested in
      isolation (`tests/test_mcp_server.py`).
- [x] `get_stock_snapshot`/`get_fo_positioning` return a structured `{"error": ...}`
      payload for an unknown/non-eligible symbol rather than raising — the model needs
      something it can read and reason about ("no F&O data" is itself informative),
      not a tool-call exception.
- [x] Each tool opens and closes its own SQLite connection — no shared connection across
      concurrent async tool calls (sqlite3.Connection is not thread/task-safe to share).
- [x] `save_insight` is the only mutating tool — confirmed not loaded into chat's
      allowlist is chat's own concern (spec 12, out of scope this iteration), but this
      spec confirms the tool exists as a single, clearly-named mutation point.

## Out of scope
- `detect_anomalies`, `scan_market`, `get_market_context`, `search_news` — additive,
  only if time permits (tech spec §5); not built this iteration.
- Authentication/authorization on tool calls — v1 has one demo user, no auth (tech spec §7).

## Assumptions / open questions
- None outstanding — DAL contracts (04/05) fully cover what these tools need.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation | M1 — written just-in-time per TECH_SPEC.md §10, once the DAL and MCP round-trip gotchas (content-block vs artifact unwrapping) were actually discovered by building |
