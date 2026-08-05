# Bhavcopy Flow Agent

**[View the Product Overview presentation →](https://claude.ai/code/artifact/4d160d1b-70a6-4b28-8abd-05c6f47fd77a)**

An autonomous analyst that reads the NSE end-of-day bhavcopy, checks only the stocks a
user holds or watches, and reports what the flow data says — in plain English, with
evidence and confidence, never a bare label. Informs; the human decides.

## Start here
- **`PRD.md`** — what we're building and why.
- **`TECH_SPEC.md`** — how it's built (architecture, stack, milestones).
- **`specs/`** — spec-driven development: one contract per component. `specs/README.md`
  is the index — check status there before assuming a component is done.
- **`TESTING.md`** — step-by-step setup and testing guide. Start here to actually run it.

## Stack, in one line
Python 3.12 + FastAPI + SQLite backend, React + Vite frontend, two LangGraph agents —
a digest `StateGraph` and a chat ReAct agent — on **free inference only** (Groq primary,
Cerebras fallback) via `langchain-mcp-adapters` against a shared MCP tool server. No paid
API dependency anywhere in the runtime stack — see `TECH_SPEC.md` §1 and §7.

## Setup
See **`TESTING.md`** for the full step-by-step guide. Quick version:
```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp ../.env.example ../.env   # add a free Groq key to actually run the digest agent
.venv/bin/python -m uvicorn app.main:app --reload

cd ../frontend
npm install
npm run dev
```
The app starts empty — no seeded demo data. Use the **"Ingest Data"** tab to bring in a
real NSE trading day, then the **Manage** tab to add real tickers to your portfolio/
wishlist (autocomplete only finds symbols you've ingested — see `TESTING.md` §5's
callout for why). `ingest/seed_data.py` still exists but is a **test-fixture generator
only** — the pytest suite uses it against disposable temp databases; it is never run
against the app's real database (see its docstring for why: an earlier version used real
NSE tickers for synthetic data and a real ingest silently corrupted it via upsert — fixed
by using clearly-fake `DEMO*` tickers, but the safer fix was just not mixing synthetic
and real data in the same database at all).

## Status
**M0–M3 built** (core vertical slice: ingest, DB/DAL, MCP server, digest LangGraph agent
with a numeric-grounding guardrail, FastAPI routes, React frontend), plus two more
features added after that, by explicit choice, once the core was working:
- **M4, partial — insight status resolution** (`agent/resolve_insights.py`, specs/11):
  every digest run now re-checks prior `pending`/`strengthening` insights against fresh
  price data and updates status/outcome — deterministic, no LLM. The 6-month historical
  **backtest** half of M4 remains out of scope (needs bulk historical NSE data this
  project has no practical way to acquire — see specs/11's Purpose).
- **M5 — chat agent** (`agent/chat_graph.py`, specs/12): a read-only LangGraph ReAct
  agent for interrogating the dashboard ("what needs my attention?", "why review X?"),
  with the same anti-fabrication grounding guardrail as the digest agent. Verified live
  against real Groq calls and real portfolio data.

See `specs/README.md` for per-component status and `TESTING.md` for the full walkthrough.

**Known limitation:** NSE's Akamai bot-detection blocks *scripted* bhavcopy downloads —
confirmed even from a normal home network, not just a build sandbox. The app includes a
manual-download workaround instead: the **"Ingest Data"** tab walks you through opening
NSE's site in your own browser, then locates and parses whatever you download — see
`TESTING.md` §7. Verified end-to-end against real NSE files (2026-08-03) — see
`specs/01_ingest_bhavcopy.md` for the full story, including a real parsing bug (NSE's
delivery CSV uses `", "` as its field separator, not a bare `","`) found and fixed
against the actual downloaded file.
