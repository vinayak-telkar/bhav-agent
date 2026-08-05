# Tech Spec — Bhavcopy Flow Agent

**Status:** Draft for review · **Date:** 2026-07-21 · **Companion:** PRD.md

---

## 1. Stack & topology

- **Frontend:** React + Vite (TypeScript).
- **Backend:** Python 3.12, FastAPI (one service), SQLite (WAL mode).
- **Agent orchestration:** LangGraph for both agents. `langchain-mcp-adapters`
  (`MultiServerMCPClient`) bridges the shared MCP server's tools into LangChain
  `BaseTool` objects — one integration pattern for both agents, provider-agnostic.
  Pin `langgraph>=0.3`, `langchain-mcp-adapters>=0.1` — both move fast; verify exact
  current API shape in the day-1 spike (§11), don't code against remembered signatures.
- **Models — free-inference-only, by design.** No Anthropic/paid API dependency anywhere
  in the runtime stack — the project must run end-to-end for anyone who clones the repo
  and gets a free API key, no paid account required. Both agents use `langchain-groq`
  (`ChatGroq`) against Groq's free tier as the primary provider, with Cerebras
  (`langchain-cerebras` — confirm exact package name in the day-1 spike, same treatment as
  every other unconfirmed API surface) as a documented fallback provider.
  - **Agent — digest (Mode 1):** default `openai/gpt-oss-120b` — the harder job
    (evidence/confidence reasoning across a multi-step drill-down) gets the stronger of
    the two default free options. Implemented as an explicit `StateGraph`, not a generic
    ReAct loop — see §6.
  - **Agent — chat (Mode 2, stretch):** default `llama-3.3-70b-versatile` — the
    best-documented, most stable free-tier option, which matters more for a
    higher-volume, lower-stakes surface than raw capability does. Built on LangGraph's
    prebuilt ReAct agent plus one custom guardrail node — see §6b.
  - **Model names are `.env` config, never hardcoded.** Free-tier catalogs churn — models
    get added, renamed, or dropped between one clone of this repo and the next. `README`
    carries a "known-good options, verify against the provider's live model list before
    running" table rather than a pinned assumption. See §11.
  - **No trust asymmetry between the two agents anymore.** Both run on free models now —
    the numeric-grounding guardrail (§6b) applies to both the digest and chat paths
    equally; see §6's updated note.
- **Tools:** a custom MCP server (Python, FastMCP) — the *only* tool surface for both agents,
  reached exclusively through `langchain-mcp-adapters`, never through a raw `mcp` client
  or provider-native tool-calling directly.
- **Ingest:** scheduled Python job (APScheduler or system cron).

```
React ──REST──▶ FastAPI ──▶ data/ (shared DAL) ──▶ SQLite (bhav.db)
                   │                                    ▲
                   │ /chat                              │
                   ▼                                    │
   agent/chat_graph.py (LangGraph ReAct, ChatGroq: llama-3.3-70b-versatile default)
                   │                                    │
                   └──MultiServerMCPClient──▶ mcp_server/server.py ◀──MultiServerMCPClient──┐
                                                          │                                   │
                                                          │    agent/digest_graph.py (LangGraph
                                                          │    StateGraph, ChatGroq: gpt-oss-120b
                                                          │    default, Cerebras fallback)
   scheduled digest job ──────────────────────────────────────────────────────┘
   ingest job ──▶ downloads NSE bhavcopy ──▶ data/ ──▶ SQLite
```

**Two front doors, one data layer.** FastAPI routes and MCP tools both call the same
`app/data/` functions. FastAPI returns JSON to React; MCP tools are thin wrappers over the
identical functions. No query is duplicated between them.

**Two agents, one MCP server, one shared orchestration pattern, both on free inference.**
`agent/digest_graph.py` and `agent/chat_graph.py` are separate `MultiServerMCPClient`
connections to the same `mcp_server/server.py`, differing only in which free model is
bound in and which tools are loaded. `langchain-mcp-adapters` converts MCP tool schemas to
LangChain `BaseTool` objects for both — swapping providers (Groq ↔ Cerebras) or models is
a config change, not a code change, given the churn risk noted in §1. `chat_graph.py`
loads a **read-only tool allowlist** (§6b) — it
never binds `save_insight`. Neither agent is a server; each exposes a plain async
function (`run_daily_digest(user_id)`, `answer(user_id, question)`) that compiles and
invokes its graph, called by the scheduler or by FastAPI `/chat` respectively.

## 2. Repo layout

```
bhav-agent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + router registration
│   │   ├── routes/              # symbols, watchlist, digest, chat
│   │   ├── data/                # shared DAL (the DRY seam)
│   │   │   ├── db.py            # connection factory, PRAGMA setup
│   │   │   ├── bars.py          # daily_bars queries + baseline windows
│   │   │   ├── fo.py
│   │   │   ├── watchlist.py
│   │   │   └── insights.py
│   │   └── schema.sql
│   ├── mcp_server/
│   │   └── server.py            # FastMCP; tools import app.data.*
│   ├── agent/
│   │   ├── digest_graph.py      # ChatGroq (Cerebras fallback): LangGraph StateGraph — run_daily_digest()
│   │   ├── chat_graph.py        # Groq (Kimi K2 → Llama 3.3 70B): LangGraph ReAct + guardrail
│   │   │                        # node — answer(), /chat, stretch
│   │   └── prompts.py           # shared system-prompt content (seven-action spec,
│   │                            # evidence/confidence rule, anti-fabrication rule —
│   │                            # both agents draw from this, chat gets a read-only subset
│   ├── ingest/
│   │   └── bhavcopy.py          # download + parse → SQLite
│   └── pyproject.toml
├── frontend/                    # React + Vite
└── data/bhav.db
```

## 3. Database

SQLite in WAL mode. 7 tables — see `schema.sql` (canonical DDL agreed in design:
`symbols`, `daily_bars`, `fo_daily`, `market_days`, `users`, `watchlist`, `insights`).
Baselines (delivery %, volume) are **computed on read** with window functions, not stored:

```sql
SELECT trade_date, deliv_pct,
       AVG(deliv_pct) OVER (
         ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
       ) AS deliv_baseline_20d
FROM daily_bars WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1;
```

`watchlist` PK is `(user_id, symbol)` — a symbol is held or wished, never both; promotion
is a one-line `UPDATE`. `insights` is per-user (prioritization depends on the user's size
bucket) and carries `status` (pending/strengthening/confirmed/expired) + `evidence_json` +
`price_at_insight` + `outcome_pct` to power the accountability tracker and the backtest.

`daily_bars.closing_price_method` (`'vwap_30min'` | `'cas_auction'`) records which
mechanism produced that day's close — added for SEBI's Closing Auction Session, live
2026-08-03 for F&O-eligible (Category I) stocks only (§4). **Ingest starts fresh from this
new regime** — the project doesn't collect or reconcile any pre-cutover data, so a
baseline window spanning both values never actually occurs in practice. The column is
recorded for completeness and future-proofing (in case the mechanism changes again, or a
later phase adds historical backfill) rather than requiring active mixed-window detection
logic in the DAL. No special baseline handling needed for v1.

## 4. Ingest (`ingest/bhavcopy.py`)

Milestone 0 and the highest external risk. Downloads three NSE end-of-day files:

- **Cash UDiFF bhavcopy** (OHLC, volume, VWAP, turnover, trades, series).
- **Security-wise delivery** (`sec_bhavdata_full`) — deliverable qty + delivery %.
- **F&O bhavcopy** — futures + options; rolled up per underlying into `fo_daily`
  (fut close/OI/OI-change, basis, PCR, max-call-OI strike, max-put-OI strike).

Requirements: idempotent (safe re-run for a date), skips non-trading days, backfills gaps,
detects corporate actions (splits/bonuses) and marks affected symbols so signal generation
suppresses/flags them. **Verify current NSE URLs and the anti-scraping requirement in the
spike — do not code against old blog-post URLs.** For the backtest, a bulk loader pulls
~6 months of archived files.

**SEBI Closing Auction Session (CAS), live from 2026-08-03.** Category I (F&O-eligible)
stocks switch closing-price computation from VWAP-of-last-30-min to an auction match on
this date; Category II (non-F&O) stocks are unaffected. Any bhavcopy file the ingest spike
touches from this date onward already reflects it — confirm on first real-file inspection
whether NSE's format changed (new columns, an explicit Category I/II flag), and populate
`daily_bars.closing_price_method` (schema §3) accordingly. Since the live digest ingests
fresh from this date forward, baselines never span both regimes in practice — no
downstream handling needed beyond recording the column (§3). **The 6-month backtest
loader (M4) is the one place this could resurface:** it pulls historical data predating
the cutover almost entirely, so the bulk of that dataset is naturally single-regime
(`'vwap_30min'`) anyway — only worth a second look if the backtest's window is later
extended close enough to 2026-08-03 that it starts crossing the boundary. Not a concern
for M0/M1; flag for whoever picks up M4.

## 5. MCP server (`mcp_server/server.py`)

FastMCP. Tools return **small (<~2KB), pre-computed JSON** with literal field names the
model can read (`delivery_pct_today`, `delivery_pct_20d_avg`, `classification`). The model
interprets and communicates; it never does arithmetic.

MVP tools (five carry the product):
- `get_stock_snapshot(symbol)` — today's OHLC, close-vs-VWAP, volume-vs-20d, delivery-vs-baseline, turnover-rank delta.
- `get_delivery_trend(symbol, days)` — rolling delivery %, deliv-qty trend, avg-trade-size trend.
- `get_fo_positioning(symbol)` — buildup classification (long/short/covering/unwinding), basis, PCR, max call/put OI strikes.
- `get_portfolio()` / `get_wishlist()` — symbol lists + size buckets.
- `get_prior_insights(symbol)` / `save_insight(symbol, insight, action, confidence, evidence)` — cross-day memory.

Additive: `detect_anomalies(symbol)`, `scan_market(criteria)`, `get_market_context()`,
`search_news(symbol)` (only if time permits).

**Transport:** `stdio` only — both agents spawn/connect to the server as a subprocess via
`MultiServerMCPClient`; no ports. (MCP's stdio transport is designed for exactly this kind
of local, single-machine setup; the optional `streamable-http` mode for a Claude Desktop
demo is cut for v1 per §8.)

## 6. Digest agent (`agent/digest_graph.py`) — Mode 1

`langchain-groq`'s `ChatGroq` (free tier), with `langchain-cerebras`'s `ChatCerebras` as an
automatic fallback on rate limit. Configuration:

- **Model + fallback:** default `openai/gpt-oss-120b` on Groq — the harder job (evidence/
  confidence reasoning across a multi-step drill-down) gets the stronger of the project's
  two default free options. On a rate-limit error, retry against Cerebras's free tier
  (model TBD at implementation time — its catalog is the most volatile of the two
  providers per §11, confirm what's actually available when this is built, don't assume
  the model checked into this spec is still live).
- **max_tokens:** ~16000 (non-streaming is fine at this size).
- **Iteration limit:** capped, same reasoning as chat's cap in §6b — open-weight models on
  free tiers are more prone to runaway tool-call loops than a frontier closed model would
  be; don't assume this needs less discipline than chat just because it's "the primary
  agent."

**Tools via MCP:** `MultiServerMCPClient({"bhav": {"command": "python", "args":
["mcp_server/server.py"], "transport": "stdio"}})` → `await client.get_tools()` returns
LangChain `BaseTool` objects for every tool the server exposes; the digest graph binds all
of them (unlike chat, it needs `save_insight`).

**Why an explicit `StateGraph`, not a generic ReAct loop:** the digest's defining
requirement (PRD §6, "agentic proof") is *conditional* drill-down — call
`get_delivery_trend`/`get_fo_positioning` only where a snapshot looks off, not for every
symbol. A generic ReAct loop leaves that decision entirely to prompt-following; an
explicit graph makes it structural, and gives you a natural, inspectable log of exactly
which nodes fired for which symbol — directly satisfying "reconciles against prior
insights — visible in logs" without extra logging plumbing.

Node shape (`StateGraph` with a per-user, per-symbol-list state):

```
fetch_portfolio ──▶ snapshot_symbol (per symbol, get_stock_snapshot)
                          │
                 conditional edge: does the snapshot look off?
                    │                                │
                   yes                               no
                    ▼                                │
        drill_down (get_delivery_trend,               │
        get_fo_positioning as needed)                 │
                    │                                │
                    └──────────────┬──────────────────┘
                                   ▼
                    compare_prior (get_prior_insights)
                                   ▼
                    write_insight (LLM synthesis: evidence +
                    confidence + action from the §4 table)
                                   ▼
                    verify_insight_grounded ──fails──▶ template_fallback
                                   │                          │
                                 passes                       │
                                   ▼                          ▼
                              save_insight ◀──────────────────┘
```

The "looks off" branch condition and the synthesis step in `write_insight` are where the
system prompt's rules (below) actually get exercised — the graph structure enforces *when*
tools get called, the prompt governs *what the model does with what comes back*.

**System prompt (`prompts.py`):** encodes the seven-action vocabulary, the signal→action
table, the three design principles, the evidence-and-confidence requirement, and the hard
rule: *quote tool numbers, never estimate; label ambiguous signatures; state the horizon.*
`closing_price_method` (§3) is recorded per row for completeness given SEBI's Closing
Auction Session change, but since ingest starts fresh from the new regime (§4), no
baseline window will ever span two methods in practice — no special prompt rule needed for
this; the column exists for future-proofing only, not active handling.

**Numeric grounding guardrail — now required on digest too, not just chat.** The earlier
version of this design left the digest path relying on the system-prompt rule alone,
reasoning that Claude on a higher-stakes path was trusted more than a free model on a
lower-stakes one. That asymmetry no longer holds — **both agents now run on free
open-weight models** (§1), so there's no basis left for treating one path's fabrication
risk as lower than the other's. Add an equivalent `verify_insight_grounded` node before
`save_insight`, same pattern as §6b's `verify_grounded`: extract every number in the
model's synthesized insight, confirm each traces back to that graph run's tool outputs,
and route anything that doesn't to a template fallback built directly from the tool JSON
rather than letting an ungrounded number reach `save_insight`. This is no longer optional
hardening for days 6–7 — it belongs in M2, alongside the rest of the digest graph, since
it's now covering the project's only reasoning path with no stronger fallback behind it.

**Mode 2 (chat) does not run through this agent** — see §6b for the separate Groq-based
chat agent.

## 6b. Chat agent (`agent/chat_graph.py`) — Mode 2, free-inference

Chat runs on a separate model from the digest, both free tier. Rationale: chat answers
questions about insights the digest already computed and persisted — it isn't the origin
of new judgment calls the way the digest is — so it tolerates a lighter model than the
digest's default, prioritizing free-tier stability and volume over raw capability. Treat
it as its own small subsystem with its own risks, not an afterthought bolted onto
`digest_graph.py`.

**Model + fallback:** `langchain-groq`'s `ChatGroq`, default `llama-3.3-70b-versatile` —
free tier is a well-documented 1,000 requests/day, 12,000 TPM, the most stable of the
project's default free-tier choices, which matters more here than on the digest path
given the higher expected call volume. On a rate-limit error, retry against Cerebras
(same fallback provider as digest, §6) rather than a second Groq model — keeps the
fallback logic identical across both agents instead of each inventing its own. Model IDs
above are illustrative — confirm exact current strings and limits before wiring the
client; see §11's note on catalog churn.

**Tools via MCP, restricted:** its own `MultiServerMCPClient` connection to
`mcp_server/server.py` (independent of the digest graph's connection — not shared, not
multiplexed), but filtered client-side to a **read-only allowlist** after
`client.get_tools()`: `get_stock_snapshot`, `get_delivery_trend`, `get_fo_positioning`,
`get_prior_insights` only. `save_insight` is never loaded into this agent's tool list —
both as a safety boundary (chat should not be able to mutate persisted insights) and
because a narrower tool surface improves tool-selection accuracy for a weaker model.

**Graph shape:** LangGraph's prebuilt `create_react_agent` (from `langgraph.prebuilt`,
confirm exact import path/name in the day-1 spike — this has moved between
`langgraph.prebuilt` and `langchain.agents` across versions) wired with the filtered tool
list and `ChatGroq`, wrapped by one additional custom node appended after the ReAct
subgraph terminates:

```
[create_react_agent subgraph: reason → tool calls → observe → loop] ──▶ verify_grounded ──▶ END
                                                                              │
                                                                    fails? ──┴──▶ template_fallback ──▶ END
```

**`verify_grounded` node (numeric grounding guardrail — required, not optional
hardening):** before the response reaches the user, extract every number in the model's
final message and confirm each one appears in the tool-call outputs collected during that
graph run (LangGraph's message state makes this straightforward — walk the `ToolMessage`
entries in state). Anything that doesn't trace back routes to `template_fallback`, which
builds a response directly from the tool JSON instead of the model's prose. This exists
because PRD §9 ("model fabricates numbers") is not a risk this design accepts anywhere —
free/open-weight models are more prone to drifting off a quoted figure under
conversational pressure than a frontier closed model would be, which is exactly why this
check exists on both agents now (§6) rather than being treated as optional.

**Iteration limit:** `create_react_agent`'s `recursion_limit` (or equivalent) capped at
~6 — free-tier models are more prone to runaway tool-call loops than a frontier model,
same reasoning as the digest graph's cap (§6).

**Fallback if this subsystem is unstable near demo day:** a small set of canned
drill-down queries ("show me why X was flagged") that render the relevant tool JSON
directly with no LLM in the loop at all. Keep this as backup, not the primary design —
chat should still attempt free-form Q&A first.

## 7. FastAPI surface

- `GET /symbols?q=` — autocomplete from `symbols`.
- `GET /watchlist`, `POST /watch`, `PATCH /watch/{symbol}`, `DELETE /watch/{symbol}`.
- `GET /digest/today`, `GET /holdings`, `GET /insights/history` — read-only dashboard feeds
  from persisted agent output (no LLM call on page load).
- `POST /chat` — invokes `agent.chat_graph.answer(...)` (Groq via LangGraph, not the
  digest's ChatGroq/gpt-oss-120b graph).

Single hardcoded demo `user_id`; no auth in v1.

## 8. Milestones

- **M0 — Ingest spike:** one real day, all three files → schema. De-risks NSE access. Gate.
  **Run alongside a second, equally load-bearing gate:** a trivial LangGraph +
  `langchain-mcp-adapters` round trip against one dummy MCP tool, to nail down the exact
  current API shape (`MultiServerMCPClient`, `create_react_agent` import path,
  `ChatGroq`/`ChatCerebras` thinking/effort/reasoning parameter names) before building anything real
  on top of it. Both gates block M1.
- **M1 — Data + MCP:** DAL, schema, five core MCP tools, unit-tested against a seeded DB.
- **M2 — Digest graph:** system prompt + `digest_graph.py`'s `StateGraph` (fetch → snapshot
  → conditional drill-down → compare prior → write insight → `verify_insight_grounded` →
  save_insight); generate a digest for a demo portfolio; persist insights. The
  grounding-check node is in scope here, not deferred — see §6's note on why the earlier
  Claude/free-model trust asymmetry no longer applies.
- **M3 — Frontend:** manage screen + digest dashboard reading persisted output.
- **M4 — Insight tracker + backtest:** status updates across runs; historical replay and
  the forward-return measurement (evaluation section).
- **M5 (stretch, days 6–7 only, first thing cut if time is short):** `chat_graph.py` —
  `create_react_agent` wired to the read-only tool allowlist plus the `verify_grounded`
  node; build the guardrail *before* demoing this, not after. Canned-query fallback (§6b)
  stays ready throughout as a non-LLM backup. Claude Desktop HTTP demo and news fusion
  remain cut for v1 (see open items).

## 10. Development process — spec-driven, feature-sliced

Every component in §2's repo layout gets a short spec in `specs/` before its
implementation starts, using `specs/00_TEMPLATE.md`. `specs/README.md` is the index:
every spec, its milestone, owner, and dependencies — check it before starting work that
depends on another spec, and update its `Status` column as work moves.

**Why this earns its keep on a 1-week timeline specifically:** the architecture already
has clean seams (DAL, MCP tool boundary, digest graph nodes, chat graph, FastAPI routes) —
this formalizes what the design implies rather than adding new structure. The concrete
payoff: a dev can build against another component's *spec* (interface + acceptance
criteria) without reading its implementation or waiting for it to exist — see
`specs/README.md`'s note on frontend building against a route contract while backend
implements it in parallel. That's where the time actually gets saved, not from the
specs themselves.

**Rules, to keep it lightweight rather than ceremonial:**
- **Timebox spec-writing to ~15–20 min per component.** A spec is a contract (interface +
  testable acceptance criteria), not a design essay. If a template section doesn't apply,
  delete it.
- **Write specs just-in-time, not all upfront.** Write a spec right before that slice of
  work starts, not all twelve on day 0 — day-0 specs for M4/M5 work would be guessing at
  facts (NSE data shape, real tool output) that only exist after M0/M1 land.
- **A stale spec is worse than no spec.** When implementation reveals the spec was wrong,
  update the spec's `Changelog` in the same commit/PR as the code fix — this is what turns
  the folder into an actual knowledge base over time rather than a plan that quietly
  diverges from reality.
- **Interface sections are the load-bearing part.** Literal function signatures / request-
  response shapes / tool schemas — specific enough that someone else can build against it
  without asking you a follow-up question.
- **Post-MVP feature changes go through the spec first.** Update the relevant spec's
  contract and acceptance criteria, then implement against the update — keeps the specs
  folder authoritative rather than the code silently drifting ahead of the docs.

## 11. Open items to confirm before M1

**Resolved by the M0 spike (2026-08-02) — see `specs/02_langgraph_mcp_spike.md` for full detail:**
- `MultiServerMCPClient` config confirmed: `{"<name>": {"transport": "stdio", "command":
  <full interpreter path>, "args": [...]}}`. `command` must not be a bare `"python"` —
  subprocess spawn skips shell PATH resolution.
- `client.get_tools()` round-trips correctly; MCP tool `.ainvoke()` returns LangChain
  content-block format (`[{"type": "text", "text": ...}]`), not a raw payload — anything
  that calls a tool directly (not through an LLM's tool loop) must unwrap before
  `json.loads()`.
- `create_react_agent` confirmed at `langgraph.prebuilt.create_react_agent`
  (`langgraph==1.2.10`).
- Hand-built `StateGraph` + `add_conditional_edges(node, condition_fn, {branch:
  target, ...})` pattern confirmed working — this is the shape `digest_graph.py` uses.
- `langchain-cerebras==0.8.2` confirmed to exist under that name.
- Reasoning/effort parameter names confirmed **not** to share a name across providers:
  `ChatGroq` uses `reasoning_format` / `reasoning_effort`; `ChatCerebras` uses
  `reasoning_effort` / `reasoning` / `disable_reasoning`.
- Versions pinned in `backend/pyproject.toml`: `langgraph==1.2.10`,
  `langchain-mcp-adapters==0.3.1`, `langchain-groq==1.1.3`, `langchain-cerebras==0.8.2`,
  `mcp==1.29.0` (not `2.0.0` — see below), `fastapi==0.141.1`, `apscheduler==3.11.3`.
- **New gotcha found, not anticipated by the original spec:** `mcp==2.0.0` removed
  `RequestContext` from `mcp.shared.context`, breaking `langchain-mcp-adapters==0.3.1`'s
  import. Pinned `mcp==1.29.0` instead. Re-verify this pair together before ever bumping
  either package.
- Scheduler choice: **APScheduler**, in-process, confirmed installed and pinned.

**Still open — require a live Groq/Cerebras API key, not available in this build
session:**
- `ChatGroq`/`ChatCerebras` binding the dummy tool and producing a correct tool call end
  to end (structural pieces confirmed; the actual model round trip was not run).
- Exact current Groq/Cerebras model ID strings and free-tier rate limits for
  `gpt-oss-120b` and `llama-3.3-70b-versatile`, verified live against each provider's
  model list — **do this before the demo**, using the README's known-good-options table
  as the checklist; don't let the `.env.example` defaults go unverified.
- NSE current URLs + access method: implemented against NSE's documented endpoints with
  session warm-up (see `ingest/bhavcopy.py` and `specs/01_ingest_bhavcopy.md`), but NSE
  returns 403 (Akamai bot-detection) to unauthenticated requests from this environment —
  **verify actual ingest success from a machine/network where NSE is reachable** before
  relying on live data; the seed-data path (`ingest/seed_data.py`) is the tested fallback
  for all downstream development in the meantime.
