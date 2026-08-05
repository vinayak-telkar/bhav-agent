# Specs index — Bhavcopy Flow Agent

Process: see workflow in TECH_SPEC.md §10. Each row below becomes its own file in this
folder using `00_TEMPLATE.md`. Status is the source of truth for "is this blocking me" —
check it before starting work that depends on another spec.

| # | Spec | Milestone | Suggested owner | Depends on | Status |
|---|---|---|---|---|---|
| 01 | Ingest (`ingest/bhavcopy.py`, `ingest/local_ingest.py`) | M0 (gate) | Dev A | — | automated path blocked by Akamai bot-detection (confirmed even from a real home network); manual-download fallback built, tested, and verified end-to-end in a live browser — see specs/01's Changelog |
| 02 | LangGraph + MCP spike | M0 (gate) | Dev B | — | done |
| 03 | DB schema (`schema.sql`) | M0/M1 | Dev A | — | done |
| 04 | DAL — bars/fo (`app/data/bars.py`, `fo.py`) | M1 | Dev A | 01, 03 | done |
| 05 | DAL — watchlist/insights (`app/data/watchlist.py`, `insights.py`) | M1 | Dev B | 03 | done |
| 06 | MCP server + 5 core tools (`mcp_server/server.py`) | M1 | Dev A | 04, 05 | done |
| 07 | Digest graph (`agent/digest_graph.py`) + system prompt | M2 | Dev B | 02, 06 | done |
| 08 | FastAPI routes (`app/routes/*`) | M2/M3 | Dev A | 04, 05 | done |
| 09 | Frontend — manage screen | M3 | Dev C (or A/B) | 08 (contract only — can mock and build in parallel) | done |
| 10 | Frontend — digest dashboard | M3 | Dev C (or A/B) | 08 (contract only) | done |
| 11 | Insight status resolution (`agent/resolve_insights.py`) | M4 (partial — backtest itself still deferred, see spec 11's Purpose) | Dev B | 05, 07 | done |
| 12 | Chat graph (`agent/chat_graph.py`) + numeric guardrail | M5 (stretch) | Dev A or B, whoever's free | 02, 06 | done — built and verified live, by explicit user choice overriding tech spec's default "cut if short on time" |

**Why frontend (09/10) only depends on the *contract* from 08, not its implementation:**
this is the actual payoff of spec-driven work here — write spec 08's request/response
shapes first, frontend builds against a mock matching that shape, backend implements
against the same spec independently. They converge without either dev reading the other's
code mid-build.

**Parallelization notes:**
- 01 and 02 run simultaneously on day 1 — unrelated risk surfaces, no shared dependency.
- 04/05 can split across two people once 03 is settled, since bars/fo and
  watchlist/insights don't share tables in a way that creates write contention.
- 09/10 can start the moment 08's *spec* exists, not when it's *implemented* — don't let
  frontend sit idle waiting on backend.
- 12 is explicitly the first thing to drop if days 6–7 run short; don't staff it until 07
  and the dashboard are solid.
