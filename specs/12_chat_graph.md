# Spec: Chat graph (`agent/chat_graph.py`) — Mode 2

**Owner:** implementation team · **Milestone:** M5 (stretch, per tech spec explicitly the
first thing to cut if time is short — built now by explicit user choice) · **Status:**
in-progress
**Depends on:** 02 (LangGraph/MCP spike), 06 (MCP server)
**Consumed by:** new `POST /chat` route, frontend chat panel

## Purpose
Lets the user interrogate the digest's output conversationally ("why did you flag X?",
"what's my portfolio's biggest risk right now?") over the same MCP tool surface the
digest agent uses, restricted to read-only lookups. Chat answers *about* insights the
digest already computed and persisted — it does not originate new judgment calls the way
the digest does, which is what justifies a lighter model and a ReAct loop instead of an
explicit `StateGraph` (tech spec §6b).

## Interface / contract
```python
async def answer(user_id: str, question: str, db_path: str | None = None) -> str:
    """Compiles and runs the chat graph for one question. Called by POST /chat."""

async def stream_answer(user_id: str, question: str, db_path: str | None = None):
    """Same, yielding progress lines as the ReAct loop reasons/calls tools —
    same UX pattern as agent.digest_graph.stream_daily_digest."""

def build_chat_graph(tools: list[BaseTool], llm: BaseChatModel):
    """Dependency-injected, same pattern as build_digest_graph — tests pass
    a fake LLM double against real MCP tools."""
```

**Read-only tool allowlist** (filtered client-side after `client.get_tools()`, `save_insight`
and `ingest_local_bhavcopy` never loaded): `get_stock_snapshot`, `get_delivery_trend`,
`get_fo_positioning`, `get_prior_insights`, `get_portfolio`, `get_wishlist`. **Deliberately
wider than tech spec §6b's literal 4-tool list** (which omits `get_portfolio`/
`get_wishlist`) — without portfolio/wishlist visibility, chat can't answer anything
portfolio-wide ("what needs my attention today?"), only single-symbol questions, which
materially undercuts the feature. Both added tools are read-only, so the safety
boundary (`save_insight` never loaded) is unaffected. Documented here per TECH_SPEC.md
§10's rule that spec changes go through the spec, not silent drift.

**Graph shape** — a compiled `create_react_agent` wrapped as a node inside an outer
`StateGraph`, per tech spec §6b's diagram:
```
entry -> react (create_react_agent subgraph: reason -> tool calls -> observe -> loop)
      -> verify_grounded -> [grounded? END : template_fallback -> END]
```
Outer state is `MessagesState` (`langgraph.graph.MessagesState`, confirmed available in
the pinned `langgraph==1.2.10` — same messages-list-with-`add_messages`-reducer shape
`create_react_agent` itself expects, which is what lets the compiled subgraph be added
directly as a node via `graph.add_node("react", react_agent)`) plus one added key:
`grounded: bool | None`.

**`verify_grounded`:** two checks, either one failing routes to `template_fallback`.
**(1) Leaked tool-call syntax** — if the final `AIMessage`'s content matches
`LEAKED_TOOL_CALL_PATTERN`/`_TAIL_PATTERN` (a `<function=...>...</function>`-shaped
string the model emitted as visible text instead of a real structured tool call — a
real, live-observed `llama-3.3-70b-versatile` failure mode, see Changelog), the message
fails unconditionally — no attempt to strip-and-keep surrounding prose, since the leak
is sometimes referenced inline as a sentence's own subject and stripping just the tag
can leave grammatically broken text behind. **(2) Numeric grounding** — walks
`state["messages"]` for `ToolMessage` entries, flattens every number out of their
content (reusing `agent.digest_graph._flatten_numbers` — same logic, not duplicated)
into a reference set; extracts every decimal number from the final `AIMessage`'s content
via the same pattern digest's grounding check uses; every candidate must be within
tolerance of some reference number. Sets `grounded` in state; `template_fallback` on
failure builds a response directly from the most recent tool output's JSON instead of
the model's prose (tech spec §6b) — or, if no tool was called at all (e.g. an
out-of-scope request that leaked instead of cleanly declining), a generic "I wasn't able
to give a clean answer" redirect.

**Threading `user_id` through the ReAct loop:** `create_react_agent` doesn't give tool
calls implicit access to values outside the message history, and this project has no
auth/session/injected-context machinery for v1 (single demo user, tech spec §7).
Simplification: the system prompt states the current `user_id` directly and instructs
the model to pass it as the `user_id` argument to any tool that needs it
(`get_portfolio`/`get_wishlist`/`get_prior_insights`). Acceptable given there's exactly
one user; would need real injected state (not prompt-stated) the moment multi-user
support is ever added.

**Tool output wrapping — a real bug, found and fixed:** every tool passed to
`create_react_agent` is wrapped by `_wrap_as_string_tool` to always return a plain JSON
string, not the raw MCP-adapter output. Cause: `create_react_agent`'s internal ToolNode
converts a tool's return value into a `ToolMessage`, and MCP list-returning tools split
into one content block per list item (confirmed in the M0 spike) — an **empty** list
produces **zero** content blocks. Confirmed live: Groq's API rejects this outright (400
— tool message content must be a string or a non-empty array), and this isn't a rare
edge case, it's what happens the first time anyone asks about a symbol/portfolio with no
results yet, which is a completely ordinary question. The wrapper calls each tool via
`agent.mcp_tools.call_tool` (same artifact-unwrapping helper the digest graph's
structural calls use, not a second implementation) and returns `json.dumps(result)` — a
string is always valid `ToolMessage` content regardless of emptiness, for any provider,
not just Groq. `_numbers_from_tool_message`/`_tool_message_raw_text` were simplified to
match (plain `json.loads(message.content)`, no content-block unwrapping needed anymore).

**Model + fallback:** `build_chat_models("CHAT", max_tokens=1024)` (same
`llm_factory.py` helper the digest graph uses; `.env`'s `CHAT_MODEL` default
`llama-3.3-70b-versatile`) — max_tokens kept small deliberately, same 8000 TPM free-tier
lesson from spec 07's Changelog; a conversational answer needs far less headroom than
1024 already gives it. `.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)`
on the compiled graph's `.ainvoke()` call site, same rate-limit reasoning as digest.

**Iteration limit:** `RECURSION_LIMIT = 15` (`agent/chat_graph.py`) — **not** the tech
spec §6b's original `~6` estimate. Confirmed live (2026-08-04): a single, entirely
ordinary tool call ("what do I hold?" → one `get_portfolio` call → final answer) already
hit `"Sorry, need more steps to process this request."` at 6. `create_react_agent` runs
as a nested subgraph inside the outer `verify_grounded` graph, and its internal
agent/tools steps count against the same `recursion_limit` passed to the outer
`.ainvoke()`/`.astream()` call — the budget has to cover the outer graph's own steps on
top of however many inner tool-call round trips the model needs, not just the inner loop
alone. 15 is still bounded, just not cutting it this close.

**System prompt (`prompts.py`'s `chat_system_prompt(user_id)`):** built from the same
shared building blocks as `DIGEST_SYSTEM_PROMPT` (`SEVEN_ACTIONS`, `SIGNAL_ACTION_TABLE`,
`DESIGN_PRINCIPLES`, `ANTI_FABRICATION_RULE`) minus the digest-specific drill-down
instruction (not applicable — chat decides its own tool calls via ReAct, there's no
structural "looks off" gate to explain), plus conversational-answer framing and the
read-only/no-new-insights disclosure. **A hard scope-boundary section sits first, before
even the identity framing** — see Changelog: a topical-relevance test alone ("is this
about the user's stocks?") isn't a strong enough gate, since "write me code to trade
PAYTM" passes it while still being completely out of scope for a read-only, informational
product (PRD §2/§8: not investment advice, no buy/sell execution).

**FastAPI:** `POST /chat` (`app/routes/chat.py`, new) — body `{"question": str}`,
response `{"answer": str}`. `GET /chat/stream?question=...` (SSE, same UX pattern as
`GET /digest/run-stream`) — `{"progress": "..."}` lines while the agent reasons/calls
tools, then a final `{"answer": "..."}`. Single hardcoded demo user, same as every other
route (tech spec §7) — no session/conversation history persisted across requests in this
iteration (each question is independent; see Out of scope).

## Acceptance criteria
- [x] A question about a symbol with a real prior insight ("why did you flag X?") calls
      `get_prior_insights`/`get_stock_snapshot`/`get_fo_positioning` and answers with
      numbers that trace back to those calls — verified with a fake tool-calling model
      against real MCP tools (`tests/test_chat_graph.py`) **and live against a real Groq
      call and real portfolio data** (2026-08-04): asked "why might PAYTM be worth
      reviewing? what does delivery data say" and got back a correctly-grounded answer
      citing real delivery%, close price, basis, and PCR figures.
- [x] A fabricated-number response (tested via a scripted fake model, same technique as
      `tests/test_digest_graph.py` but dynamically inspecting message history — a fixed
      response list can't work here since `create_react_agent` drives a real multi-turn
      loop) is caught by `verify_grounded` and replaced by `template_fallback`'s
      tool-data-only response.
- [x] `save_insight` and `ingest_local_bhavcopy` are never in the tools list bound to this
      agent — asserted directly against the filtered tool list in a test.
- [x] A portfolio-wide question ("what do I currently hold?") successfully calls
      `get_portfolio` — confirmed both in a fake-model test and live (2026-08-04, real
      8-symbol portfolio, correct answer).
- [x] `RECURSION_LIMIT` actually bounds runaway loops — confirmed by the live discovery
      that too-low a limit (the original 6) genuinely does cut off a legitimate run
      ("Sorry, need more steps to process this request."), proving the cap is real and
      active, not a no-op. No separate synthetic infinite-loop test was written on top of
      that live evidence.

## Out of scope
- Multi-turn conversation memory / session persistence — every `POST /chat` call is a
  fresh, independent question in this iteration. A follow-up like "what about last week"
  won't have prior turns to refer to. Revisit with a `checkpointer` (LangGraph supports
  this natively) if multi-turn becomes a priority.
- Any UI for reviewing past chat conversations — the frontend panel is a single
  ask-and-see-the-answer interaction, not a saved chat history.
- Canned-query non-LLM fallback (tech spec §6b's "if this subsystem is unstable near demo
  day" backup) — not built; the live model path is what's being delivered.

## Assumptions / open questions
- `CHAT_MODEL`'s free-tier TPM/RPD limits for `llama-3.3-70b-versatile` — not reverified
  live in this session (same caveat as spec 02's Changelog for the digest model); if
  chat hits the same 413/429 pattern digest did, the fix is the same (lower `max_tokens`,
  the retry wrapper already in place should absorb transient 429s).

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-04 | Initial spec | User asked what's next after the core M0-M3 build; chose chat agent (Mode 2) alongside insight status resolution (spec 11), overriding tech spec §5's "first thing cut if time is short" default by explicit choice |
| 2026-08-04 | Implemented and verified live. Two real bugs found and fixed against a live Groq call, both documented in place above: **(1)** empty-list MCP tool results produce zero `ToolMessage` content blocks, which Groq's API rejects outright — fixed by wrapping every tool to return a plain JSON string (`_wrap_as_string_tool`). **(2)** `recursion_limit=6` (this spec's original estimate) cut off even a single ordinary tool call — raised to `RECURSION_LIMIT = 15`, since nested-subgraph steps count against the same budget as the outer graph's own steps. Also noted: `create_react_agent` is deprecated in the pinned `langgraph==1.2.10` in favor of `langchain.agents.create_agent` (still functional, warning only) — flag for whoever next bumps the `langgraph` pin. |
| 2026-08-04 | **Real scope-boundary bug, found via actual user testing, not a synthetic test case.** Asked "can you write me code to automate this trading process?" — the model didn't decline; it partially engaged, describing the portfolio and offering soft advice-toned language ("I would recommend exercising caution..."), because the prompt's only out-of-scope guard was "unrelated to the user's held/watched stocks," and a trading-automation request *is* topically about the user's stocks, so it passed that check. Fixed by adding an explicit hard-boundary section to `chat_system_prompt`, checked first, before the identity/tool framing: not a financial advisor, not a coding/automation assistant, decline (briefly, with no partial attempt) requests to write code/automate trades, give personalized buy/sell advice, or anything else beyond explaining existing flow data/insights — explicitly stating that topical relevance to the user's stocks does not put a request in scope by itself. Re-verified live with the exact reported question (clean decline) plus two more phrasings ("should I buy more PAYTM right now?", "write a Python script to short ASIANPAINT automatically") — both declined correctly — and one legitimate data question ("what does the delivery trend for PAYTM look like?") to confirm the tightened prompt doesn't over-trigger on real questions. No offline unit test added for this — prompt-following behavior needs a live model to mean anything; a fake model's behavior here would just be re-testing my own script, not the actual guard. |
| 2026-08-05 | **Real bug, found via user testing again: raw internal tool-call syntax leaking into the visible answer.** Asked "tell me a financial joke" — got back a real decline sentence followed by literal `<function=get_portfolio>{"user_id": "demo-user-0001"}</function>` text. Root cause: `llama-3.3-70b-versatile` on Groq sometimes emits a tool call as text embedded in its content instead of a proper structured `tool_calls` entry; `create_react_agent` doesn't recognize unstructured text as a real tool call, so it passes the malformed content straight through as the final answer, and the previous `verify_grounded` had no check for this (no numbers to flag, so it read as trivially "grounded"). **First fix attempt (strip-and-keep) was reverted**: stripping just the `<function=...>` substring and keeping surrounding prose worked for a trailing leak, but broke when the model referenced the tag inline as a sentence's own subject ("using the `<tag>` or `<tag>` functions" → "using the  or  functions" once stripped) — confirmed live. **Final fix:** `verify_grounded` now detects the leak pattern (`LEAKED_TOOL_CALL_PATTERN`/`_TAIL_PATTERN`) and routes the *entire* message to `template_fallback` unconditionally, same as any other ungrounded response — no attempt to salvage partial prose. Re-verified live across multiple runs (the leak is stochastic, not every call triggers it): when it did leak, the fallback text was shown cleanly; when the model declined directly (no leak), that worked too. Added `tests/test_leaked_tool_call_syntax_always_falls_back` (parametrized: trailing leak, inline-subject leak, leak-only) to `tests/test_chat_graph.py`, since — unlike the scope-boundary prompt fix above — this is a *mechanical* guardrail, not prompt-following behavior, so a fake-model test is meaningful here. |
