# Spec: LangGraph + langchain-mcp-adapters round-trip spike

**Owner:** implementation team · **Milestone:** M0 (gate) · **Status:** done
**Depends on:** nothing — deliberately a throwaway spike, runs in parallel with the
ingest spike, not sequential with it
**Consumed by:** `agent/digest_graph.py`, `agent/chat_graph.py` (both blocked on this)

## Purpose
Prove the exact current API shape of `langchain-mcp-adapters` + LangGraph against one
dummy tool, before either real agent is built on top of remembered/assumed signatures.
This is throwaway code — it does not need to be clean, it needs to be *correct about the
API surface*.

## Interface / contract
Not a reusable interface — this spec's "contract" is a checklist of confirmed facts to
hand off to whoever builds `digest_graph.py` / `chat_graph.py`:

```python
# dummy_mcp_server.py — one trivial tool, e.g. add(a: int, b: int) -> int
# dummy_spike.py — connects via MultiServerMCPClient, binds the tool to both
#                  ChatGroq and ChatCerebras, drives one full call→execute→respond
#                  round trip through a minimal create_react_agent, confirms the
#                  StateGraph-building pattern works for a hand-built 2-node graph too.
```

## Acceptance criteria
- [x] `MultiServerMCPClient` config format confirmed (stdio transport, exact dict shape).
      `{"<name>": {"transport": "stdio", "command": <str>, "args": [<str>, ...]}}`.
      **`command` must be a full interpreter path (e.g. `sys.executable`), not the bare
      string `"python"`** — subprocess spawn does not do shell PATH resolution; a bare
      `"python"` raises `FileNotFoundError`. Confirmed via `dummy_spike.py`.
- [x] `client.get_tools()` confirmed to return usable LangChain `BaseTool` objects against
      the dummy tool — a call round-trips correctly. **Important gotcha, refined further
      while building the real MCP server (spec 06):** `BaseTool.ainvoke(plain_args_dict)`
      on an MCP-backed tool returns LangChain's content-block format, but the shape
      *differs by return type* — a dict-returning tool yields one content block
      (`[{"type": "text", "text": "<json>", ...}]`); a **list**-returning tool is split
      into **one content block per list element**, which breaks a naive
      `result[0]["text"]` unwrap for anything list-shaped (`get_portfolio`,
      `get_wishlist`, `get_prior_insights` all return lists). The robust fix: invoke with
      the full `ToolCall` dict form — `tool.ainvoke({"name":, "args":, "id":, "type":
      "tool_call"})` — which returns a `ToolMessage` whose `.artifact` carries
      `structured_content.result` (the clean, already-typed payload) whenever the return
      type is a list; for a dict return, `.artifact` is `None` and the single content
      block's `text` is the JSON to parse. See `agent/mcp_tools.py`'s `call_tool()` helper
      — the one place this distinction is handled, used by every direct
      (non-LLM-routed) tool call in the codebase.
- [x] `create_react_agent` import path confirmed for the pinned `langgraph==1.2.10`:
      `from langgraph.prebuilt import create_react_agent`.
- [x] A hand-built 2-node `StateGraph` (not the prebuilt agent) with one conditional edge
      compiles and runs correctly against the dummy tool — proves the pattern
      `digest_graph.py` needs (§6 of tech spec). Pattern: `add_conditional_edges(node,
      condition_fn, {branch_name: target_node, ...})`.
- [ ] `ChatGroq` and `ChatCerebras` both bind the dummy tool and produce a correct tool
      call — **not run in this sandbox** (no Groq/Cerebras API key available yet). Both
      classes import and instantiate cleanly; constructor field inspection confirms the
      params below. Re-run this specific check once a `GROQ_API_KEY` is available, before
      the demo — see spec's Assumptions.
- [ ] Exact current model ID strings for the project's default free models
      (`gpt-oss-120b`, `llama-3.3-70b-versatile`, and whatever's chosen on Cerebras) —
      **not verified live against provider model lists in this session** (would need an
      API key / account access). README's known-good-models table must be verified before
      the demo, not assumed from this spec.
- [x] Exact parameter names for reasoning/effort controls confirmed via `model_fields`
      inspection (`langchain-groq==1.1.3`, `langchain-cerebras==0.8.2`):
      - `ChatGroq`: `reasoning_format: Literal['parsed','raw','hidden'] | None`,
        `reasoning_effort: str | None`.
      - `ChatCerebras`: `reasoning_effort: Literal['low','medium','high'] | None`,
        `reasoning: dict | None`, `disable_reasoning: bool | None`.
      — the two providers do **not** share a parameter name; don't assume parity.
- [x] All of the above documented back into tech spec §11 (open items) as resolved.

**Package-pin gotcha found during this spike (not anticipated by the spec's own
Assumptions section):** `mcp==2.0.0` removed `RequestContext` from `mcp.shared.context`,
which `langchain-mcp-adapters==0.3.1` still imports — that combination is broken. Pinned
`mcp==1.29.0` instead (see `pyproject.toml`). Re-verify this pair together if either
package is ever bumped.

## Out of scope
- Any real MCP tool logic — dummy tool only.
- Groq rate-limit fallback logic — separate spec (`chat_graph.py`'s own spec).
- Anything related to the digest's actual signal/insight logic.

## Assumptions / open questions
- `langgraph>=0.3`, `langchain-mcp-adapters>=0.1`, `langchain-groq`, `langchain-cerebras`
  (exact package name unconfirmed — verify it exists under this name) pinned before this
  spike starts — if the spike reveals a materially different API on the pinned version,
  re-pin deliberately rather than floating to "whatever's newest," to avoid re-breaking
  mid-week.

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-07-23 | Initial spec | M0 gate — this framework layer is newer/faster-moving than the raw provider SDKs it replaces; resolve before building real agents on top of it |
| 2026-08-02 | Replaced ChatAnthropic with ChatGroq/ChatCerebras throughout | Project moved to a free-inference-only stack (no paid API dependency) so the repo runs for anyone who clones it; both agents now default to free models with a documented fallback pair, not one paid model plus one free model |
| 2026-08-02 | Spike run, most acceptance criteria confirmed and checked off | Real `MultiServerMCPClient`/`StateGraph` round trip run against a dummy tool (see `dummy_mcp_server.py`/`dummy_spike.py` in this session's scratch dir, not checked into repo — throwaway per spec). Found: `mcp` must stay `<2.0.0` for compatibility with `langchain-mcp-adapters==0.3.1`; `command` in `MultiServerMCPClient` config must be a full interpreter path, not `"python"`; MCP tool `.ainvoke()` returns LangChain content-block format requiring an unwrap step. ChatGroq/ChatCerebras live-call and model-ID verification deferred — no API key available in this environment; must be re-run before demo. |
