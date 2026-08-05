# Testing guide — Bhavcopy Flow Agent

This walks through setting up and testing everything built: ingest, DB/DAL, the MCP
server, the digest LangGraph agent, insight status resolution, the chat agent, FastAPI
routes, and the React frontend. The 6-month historical backtest remains out of scope —
see `README.md`'s Status section.

**The app starts empty — there is no seeded demo data.** The working order is: **ingest
real NSE data first (§6) → Manage screen, to add real tickers (§7) → Dashboard, to see
them (§7)**. Symbol search in Manage only finds symbols already ingested into the local
DB — it's not a live NSE lookup — so skipping straight to Manage before ingesting
anything will find nothing. See §7's callout for the full explanation, and
`ingest/seed_data.py`'s docstring for why synthetic seed data isn't part of this flow
anymore (a real bug: it used to use real NSE tickers, and a real ingest silently
corrupted the synthetic baseline for those symbols via upsert).

## 0. Prerequisites

- **Python 3.12+**. If your system `python3` is older, this project used
  [`uv`](https://docs.astral.sh/uv/) to manage a 3.12 virtualenv without touching system
  Python — install it if you don't have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node 18+** and npm, for the frontend.
- **(Optional, only needed to run the real digest agent)** a free Groq API key from
  [console.groq.com](https://console.groq.com) — no credit card required. A Cerebras key
  from [cloud.cerebras.ai](https://cloud.cerebras.ai) is an optional fallback (see §8 —
  not required for a normal digest run even without one).

## 1. Backend setup

```bash
cd bhav-agent/backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

This installs the pinned versions in `pyproject.toml` — see `specs/02_langgraph_mcp_spike.md`
for why these exact versions matter (a newer `mcp` breaks `langchain-mcp-adapters`).

No seeding step — starting the backend server (§4) initializes an empty schema and
bootstraps the single demo user row (`app/data/db.py`'s `ensure_demo_user`), nothing more.

## 2. Run the backend test suite

```bash
.venv/bin/python -m pytest -v
```

You should see **68 tests pass** across:
- `tests/test_schema.py` — DB constraints, seed-fixture data shape.
- `tests/test_dal.py` — DAL query logic (baselines, F&O positioning, watchlist validation).
- `tests/test_ingest.py` — NSE file-format parsers, verified against real downloaded
  files (see §10 below for detail on what was found and fixed).
- `tests/test_local_ingest.py` — the manual-download fallback's file-discovery matching
  (case-insensitivity, browser "(1)" duplicate suffixes) and load logic.
- `tests/test_ingest_routes.py` — the manual-download flow's FastAPI routes.
- `tests/test_mcp_server.py` — real `MultiServerMCPClient` round trip against the MCP server.
- `tests/test_digest_graph.py` — the full digest `StateGraph` against real MCP tools, using
  a fake LLM double (no API key needed) — proves routing, the "looks off" drill-down
  decision, and the grounding-check/template-fallback path all work.
- `tests/test_resolve_insights.py` — deterministic status resolution (confirmed/expired/
  strengthening/untouched), no LLM involved.
- `tests/test_chat_graph.py` — the chat ReAct agent against real MCP tools, using a
  scripted fake model that inspects message history dynamically (a fixed response list
  can't work here — `create_react_agent` drives a real multi-turn loop).
- `tests/test_chat_routes.py` — `POST /chat` and `GET /chat/stream` via `TestClient`.
- `tests/test_api.py` — FastAPI routes via `TestClient`.

All of these run against disposable temp databases seeded with `ingest/seed_data.py`'s
synthetic fixture data (5 `DEMO*`-prefixed symbols — deliberately not real tickers, see
that module's docstring) — never against the app's real database. None of these tests
call a real Groq/Cerebras endpoint or nseindia.com.

## 3. Run the backend server

```bash
cd bhav-agent
cp .env.example .env   # fill in GROQ_API_KEY if you want to test the real digest agent
cd backend
.venv/bin/python -m uvicorn app.main:app --reload
```

Server runs on `http://localhost:8000`. Quick smoke test:

```bash
curl http://localhost:8000/watchlist
```

Should return `[]` — empty, until you've added something via Manage (§7).

## 4. Run the frontend

```bash
cd bhav-agent/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The backend must already be running (step 3) — CORS is
pre-configured for this pairing.

## 5. What you'll see before ingesting anything

The Dashboard will show an empty portfolio and wishlist, and "no insights recorded yet"
— expected, not a bug. Go straight to §6.

## 6. Getting real NSE data in (manual-download flow)

NSE's Akamai bot-detection blocks scripted downloads — confirmed even from a normal home
network, not just a sandboxed build environment (see `specs/01_ingest_bhavcopy.md`'s
Changelog). The practical workaround, built into the app: you download the files
yourself via your own ordinary browser, and the app locates + parses + loads them.
**Verified end-to-end against real NSE files** (2026-08-03) — see §10 for two real bugs
this surfaced and fixed.

1. Open the **"Ingest Data"** tab.
2. Pick a trade date (defaults to the last *completed* trading day, not today — NSE's
   own publish lag means today's files often aren't out yet; see the note in the UI).
3. For each of the three file cards, click **"Open direct link"** — this tries NSE's
   documented URL directly. If it 404s, that specific date's file likely isn't published
   yet (F&O tends to lag cash/delivery) — try an earlier date. If it 404s across every
   date, click **"Browse NSE reports"** instead and find the equivalent file manually.
4. Click **"Check downloads folder"** — it scans `~/Downloads` (or a folder you specify)
   and tries to match each file by name. If a file isn't auto-matched, pick it from the
   "other recent files" dropdown, or paste the full path directly.
5. Click **"Parse & Import"**. You'll see a success message with the symbol count, or a
   clear error message (e.g. a file path that doesn't exist).

**Include all three files.** The cash bhavcopy is required; delivery and F&O are
technically optional, but skipping the delivery file means `deliv_pct` is `None` for
every symbol from that ingest — and delivery% is central to almost every signal this
product detects (PRD §4). The digest agent handles missing delivery% correctly (it won't
fabricate a number it doesn't have — see §8), but you'll mostly get low-confidence
"Review the position" fallbacks instead of real accumulation/distribution reads.

A single day's ingest populates `symbols` with every stock that traded that day
(1,500–2,000+ NSE equities) — that's what makes them searchable in Manage (§7). But a
single day gives every symbol a nearly-empty 20-session baseline; ingest a few more days
(any dates, run "Parse & Import" again for each) for the delivery%/volume baselines to
mean something.

## 7. Manage screen + Dashboard, with real data

> Symbol search in Manage only finds symbols already present in the `symbols` table — it
> is **not** a live lookup against NSE or any external service (by design, PRD §5:
> autocomplete is served from our own ingested symbol table). If §6 hasn't been run yet,
> this table is empty and search will find nothing.

1. Search for any real symbol from the day(s) you ingested (a few letters of its ticker)
   — it should show up in autocomplete.
2. Add it as Held (with a size bucket) or Wishlist.
3. On a wishlist row, click "I bought it" to promote it to Held.
4. Open the **Dashboard** — your portfolio/wishlist should now show real delivery%/volume
   data. "Insight tracker" still says "no insights recorded yet" until you run the digest
   (§8).

## 8. Running the real digest agent (needs a Groq API key)

1. Get a free key at [console.groq.com](https://console.groq.com), put it in
   `bhav-agent/.env` as `GROQ_API_KEY=...`.
2. Restart the backend (so it picks up the new `.env`).
3. Either click **"Run digest now"** on the dashboard, or from `backend/`:
   ```bash
   .venv/bin/python -m agent.digest_graph demo-user-0001
   ```
   Clicking the button streams live progress (one line per symbol per step —
   "Checking X…", "X looks unusual — pulling delivery trend…", "Saved X: …")
   instead of a blank spinner; see `specs/07_digest_graph.md`'s Changelog for
   why (`GET /digest/run-stream`, Server-Sent Events).
4. Refresh the dashboard — "Insight tracker" and "Needs attention" should now show real,
   evidence-backed insights for whatever you added in §7.

**Every run also re-checks prior insights first** (`agent/resolve_insights.py`, specs/11)
— you'll see "Re-checking N prior insight(s)…" lines in the progress log before the new
symbols start. No LLM involved (pure price-vs-baseline arithmetic); a fresh insight needs
a few sessions of new data before its status can move off "pending" — that's expected,
not stuck. The Insight Tracker's new "Outcome" column shows the live `outcome_pct` once
there's something to show.

**Verified live** (2026-08-03, real API key, real ingested portfolio) — see
`specs/07_digest_graph.md`'s Changelog for two real bugs this surfaced and fixed:
Groq's free tier caps at 8000 tokens/minute per request for `gpt-oss-120b`, well under
the tech spec's original `max_tokens=16000` estimate (every call failed outright, a 413,
not just under load) — lowered to 2000. Processing several symbols back-to-back can also
exceed the *cumulative* per-minute cap (a 429); rather than requiring a Cerebras key for
this, `write_insight`'s LLM call now retries with exponential backoff
(`.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)`) — TPM limits reset
every minute, so waiting out Groq's own suggested delay and retrying succeeds on its
own. **A Cerebras key remains optional**, not required for a normal digest run.

If you still see repeated rate-limit failures after these fixes (e.g. a much larger
portfolio than this demo's), a `CEREBRAS_API_KEY` + `DIGEST_FALLBACK_MODEL` in `.env`
gives a second provider to fall back to on top of the retry.

## 9. Chatting with the agent (same Groq API key)

Open the **"Chat"** tab and ask something — try the example buttons ("What do I
currently hold?", "What needs my attention right now?") or type your own question about
a symbol. Answers stream live (same SSE pattern as the digest's progress log): a
"Looking into that…" placeholder while the agent reasons and calls tools, then the real
answer.

Chat is **read-only** — it can look up snapshots, delivery trends, F&O positioning,
prior insights, and your portfolio/wishlist, but it can never save a new insight
(`save_insight` and `ingest_local_bhavcopy` are never loaded into its tool list — see
`specs/12_chat_graph.md`). Every number it states is checked against that turn's tool
output before you see it; if a draft answer doesn't check out, you'll see a note that it
switched to raw data instead of guessing.

**Verified live** (2026-08-04, real API key, real 8-symbol portfolio) — see
`specs/12_chat_graph.md`'s Changelog for two real bugs this surfaced and fixed: an empty
tool result (e.g. a portfolio with nothing in it, or a symbol with no prior insights) was
rejected outright by Groq's API (every MCP tool now returns a plain JSON string instead
of the format that broke on empty lists); and the original `recursion_limit` estimate cut
off even a single ordinary tool call, raised from 6 to 15.

## 10. What's tested vs. what needed live verification

- **Live NSE ingest URLs** — all three (cash, delivery, F&O) confirmed correct against
  real downloads (2026-08-03). A 404 on a specific date usually means that date's file
  isn't published yet, not that the URL is wrong.
- **Delivery file parsing — a real bug, found and fixed.** NSE's `sec_bhavdata_full` CSV
  uses `", "` (comma + space) as its field separator, not a bare `","`. Python's
  `csv.DictReader` treated the leading space as part of every column name after the
  first, so `SERIES`/`DELIV_QTY`/`DELIV_PER` lookups silently failed instead of raising —
  every row got skipped, `deliv_pct` came back `None` for every symbol, with no error at
  all. Fixed with `skipinitialspace=True`; verified against the real file afterward (2,409
  symbols parsed correctly). The cash/F&O UDiFF files don't have this issue (clean `,`
  delimiter, also confirmed against real files).
- **Live Groq calls** — verified (§8); two real bugs found and fixed there too
  (token-limit sizing and rate-limit retry — see §8's detail).
- **NSE's automated (scripted) download path** — confirmed blocked by Akamai
  bot-detection, from this environment and from a real home network. Not expected to
  work as-is; the manual-download flow (§6) is the supported path, and its parsing logic
  is the same code, now verified against real files (above).

## Troubleshooting

- **"missing required .env setting"** when running the digest agent — `.env` doesn't
  exist or is missing `DIGEST_PROVIDER`/`DIGEST_MODEL`; `cp .env.example .env` and fill
  in `GROQ_API_KEY`.
- **Groq call fails with a 413 ("request too large")** — your account's free-tier TPM
  limit for the configured model is lower than `max_tokens` requests; lower
  `max_tokens` in `agent/digest_graph.py`'s `run_daily_digest` (currently 2000, already
  reduced once from the tech spec's original 16000 estimate — see §8).
- **Groq call fails with a 429 after several retries** — the retry-with-backoff
  (§8) gives up after 5 attempts; either a much larger portfolio than this demo's is
  exceeding the account's per-minute budget even with backoff, or the account's TPM
  limit is unusually low. Add a `CEREBRAS_API_KEY`/`DIGEST_FALLBACK_MODEL` for a second
  provider, or reduce portfolio size.
- **Backend won't start / port 8000 in use** — another uvicorn instance is likely still
  running; `pkill -f "uvicorn app.main:app"` and retry.
- **"SQLite objects created in a thread can only be used in that same thread"** — a real
  bug, found and fixed (`app/data/db.py`'s `get_connection()` now passes
  `check_same_thread=False`): FastAPI dispatches sync dependencies (`app/deps.py`'s
  `get_db`) and sync route handlers to threadpool workers as separate calls with no
  guaranteed thread affinity, so a connection opened in one call could get used from a
  different thread in the next. If you're on an older checkout, pull the fix; this isn't
  something you can work around from the outside.
- **"Ingest Data" tab's direct NSE links 404** — most likely that date's file isn't
  published yet (§6), not a broken URL (verified correct, §10). Try an earlier date, or
  click "Browse NSE reports" to check manually.
- **"Check downloads folder" doesn't find a file you know is there** — the matcher looks
  for the expected filename pattern case-insensitively and tolerates a browser's `" (1)"`
  duplicate suffix, but NSE may have renamed the file entirely; it'll show up in the
  "other recent files" dropdown instead (anything in the folder modified in the last 48h)
  — pick it from there or paste the full path.
- **Ingested delivery data but `deliv_pct` is still `None`** — this was a real bug (§10,
  the comma-space CSV format), fixed in `ingest/bhavcopy.py`. If you're on an older
  checkout, pull the fix; if you're already on the fix and still see this, verify the
  delivery file's header row starts with `SYMBOL, SERIES, ...` (comma+space) — if NSE has
  since changed the format again, `_parse_delivery_file`'s column names are the first
  thing to check against the actual file.
