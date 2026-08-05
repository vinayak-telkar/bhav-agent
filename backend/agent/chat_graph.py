"""
Mode 2 — interrogation. LangGraph prebuilt ReAct agent on ChatGroq (default
llama-3.3-70b-versatile), Cerebras fallback. Read-only tool allowlist (no
save_insight, no ingest_local_bhavcopy). verify_grounded node before
responding, same anti-fabrication discipline as the digest graph.
See TECH_SPEC.md §6b and specs/12_chat_graph.md.
Exposes: answer(user_id, question) -- called by POST /chat.
        stream_answer(user_id, question) -- called by GET /chat/stream (SSE).
"""
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.digest_graph import _flatten_numbers  # noqa: E402 — reused, not duplicated
from agent.llm_factory import build_chat_models  # noqa: E402
from agent.mcp_tools import call_tool as call_mcp_tool  # noqa: E402
from agent.prompts import chat_system_prompt  # noqa: E402

load_dotenv()

MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"

# Read-only: save_insight and ingest_local_bhavcopy are never loaded here —
# the safety boundary from tech spec §6b. Wider than the tech spec's literal
# 4-tool list (adds get_portfolio/get_wishlist) — see specs/12's Interface
# section for why: without portfolio visibility chat can't answer anything
# portfolio-wide, only single-symbol questions.
READ_ONLY_TOOLS = {
    "get_stock_snapshot",
    "get_delivery_trend",
    "get_fo_positioning",
    "get_prior_insights",
    "get_portfolio",
    "get_wishlist",
}

# NOT the tech spec's original ~6 estimate (§6b) — confirmed live (2026-08-04) that a
# single, entirely ordinary tool call ("what do I hold?" -> one get_portfolio call ->
# final answer) already hit "Sorry, need more steps to process this request." at 6.
# create_react_agent runs as a nested subgraph inside the outer verify_grounded graph,
# and its internal agent/tools steps count against the same recursion_limit passed to
# the outer .ainvoke()/.astream() call — the budget needs to cover the outer graph's own
# steps (react, verify_grounded, maybe template_fallback) on top of however many
# inner tool-call round trips the model needs, not just the inner loop alone. 15 is
# still bounded (not unlimited), just not cutting it this close.
RECURSION_LIMIT = 15

DECIMAL_NUMBER_PATTERN = re.compile(r"-?\d+\.\d+")

# Confirmed live (2026-08-04): llama-3.3-70b-versatile on Groq sometimes emits a tool
# call as literal text in its final answer instead of a proper structured tool_calls
# entry — e.g. '...I can look up your portfolio using the
# <function=get_portfolio>{"user_id": "..."}</function> function.' create_react_agent
# doesn't recognize this as a real tool call (no structured tool_calls field), so it
# treats the malformed text as the final answer and it would otherwise reach the user
# verbatim — a raw internal-implementation-detail leak, not a data problem. Any message
# matching this is routed straight to template_fallback (see verify_grounded) rather
# than stripped-and-kept: the leaked tag is often referenced inline as a sentence's
# subject ("...using the <tag> or <tag> functions"), so removing just the tag can leave
# grammatically broken text behind — also confirmed live. Covers a closed tag, and a
# truncated/unclosed one at the end of the message (seen both ways).
LEAKED_TOOL_CALL_PATTERN = re.compile(r"<function=.*?</function>", re.DOTALL)
LEAKED_TOOL_CALL_TAIL_PATTERN = re.compile(r"<function=.*$", re.DOTALL)


class ChatState(MessagesState):
    grounded: bool | None


def _wrap_as_string_tool(tool: BaseTool) -> BaseTool:
    """create_react_agent's tool-calling loop sends raw tool output back to
    the model as a ToolMessage. MCP list-returning tools split into one
    content block per list item (confirmed in the M0 spike, specs/02) — an
    EMPTY list produces ZERO content blocks, which Groq's API rejects
    outright (400: tool message content must be a string or a non-empty
    array). Confirmed live (2026-08-04) asking about a symbol/portfolio with
    no results — not a rare edge case, a normal first question. Re-wrapping
    every tool to always return a plain JSON string sidesteps this for any
    provider, not just Groq: a string is always valid ToolMessage content
    regardless of whether the underlying result was empty. Uses
    agent.mcp_tools.call_tool (the same artifact-unwrapping helper the
    digest graph's structural calls use) so this isn't a second, divergent
    unwrapping implementation."""

    async def _run(**kwargs):
        result = await call_mcp_tool(tool, **kwargs)
        return json.dumps(result)

    return StructuredTool.from_function(
        coroutine=_run, name=tool.name, description=tool.description, args_schema=tool.args_schema
    )


def _numbers_from_tool_message(message: ToolMessage) -> set[float]:
    try:
        parsed = json.loads(message.content)
    except (json.JSONDecodeError, TypeError):
        return set()
    return _flatten_numbers(parsed)


def _tool_message_raw_text(message: ToolMessage) -> str:
    return str(message.content)


def build_chat_graph(tools: list[BaseTool], llm: BaseChatModel, user_id: str):
    tools = [_wrap_as_string_tool(t) for t in tools]
    react_agent = create_react_agent(llm, tools, prompt=chat_system_prompt(user_id))

    async def verify_grounded(state: ChatState) -> dict:
        final = state["messages"][-1]
        if not isinstance(final, AIMessage):
            return {"grounded": True}

        content = final.content if isinstance(final.content, str) else str(final.content)

        if LEAKED_TOOL_CALL_PATTERN.search(content) or LEAKED_TOOL_CALL_TAIL_PATTERN.search(content):
            # Always fall back rather than surgically stripping-and-keeping: tried
            # that first, but the leaked tag is often referenced inline as a
            # sentence's subject ("...using the <tag> or <tag> functions"), and
            # removing just the tag leaves grammatically broken text ("using the
            # or  functions") — confirmed live (2026-08-04). A clean templated
            # decline beats a technically-truthful but broken sentence.
            return {"grounded": False}

        reference: set[float] = set()
        for m in state["messages"]:
            if isinstance(m, ToolMessage):
                reference |= _numbers_from_tool_message(m)

        candidates = [float(x) for x in DECIMAL_NUMBER_PATTERN.findall(content)]
        grounded = all(any(abs(c - r) <= max(0.05, abs(r) * 0.01) for r in reference) for c in candidates)
        return {"grounded": grounded}

    async def template_fallback(state: ChatState) -> dict:
        tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        if not tool_messages:
            text = (
                "I wasn't able to give a clean answer to that. I can help explain this "
                "portfolio's flow data or past insights — try asking about a symbol or "
                "what needs attention."
            )
        else:
            text = (
                "My answer didn't check out against the data I looked up, so here's the "
                f"raw data instead: {_tool_message_raw_text(tool_messages[-1])}"
            )
        return {"messages": [AIMessage(content=text)]}

    graph = StateGraph(ChatState)
    graph.add_node("react", react_agent)
    graph.add_node("verify_grounded", verify_grounded)
    graph.add_node("template_fallback", template_fallback)
    graph.set_entry_point("react")
    graph.add_edge("react", "verify_grounded")
    graph.add_conditional_edges(
        "verify_grounded",
        lambda s: "pass" if s["grounded"] else "fail",
        {"pass": END, "fail": "template_fallback"},
    )
    graph.add_edge("template_fallback", END)
    return graph.compile()


async def _compile_graph_for_chat(user_id: str, db_path: str | None = None):
    env = {"DATABASE_PATH": db_path} if db_path else {}
    client = MultiServerMCPClient(
        {
            "bhav": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(MCP_SERVER_PATH)],
                "env": env,
                "cwd": str(MCP_SERVER_PATH.parent.parent),
            }
        }
    )
    all_tools = await client.get_tools()
    tools = [t for t in all_tools if t.name in READ_ONLY_TOOLS]
    # Same free-tier TPM lesson as the digest graph (specs/07's Changelog) —
    # a conversational answer needs far less headroom than even this.
    primary, fallback = build_chat_models("CHAT", max_tokens=1024)
    llm = primary.with_fallbacks([fallback]) if fallback else primary
    return build_chat_graph(tools, llm, user_id)


async def answer(user_id: str, question: str, db_path: str | None = None) -> str:
    graph = await _compile_graph_for_chat(user_id, db_path)
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": RECURSION_LIMIT}
    )
    final = result["messages"][-1]
    return final.content if isinstance(final.content, str) else str(final.content)


async def stream_answer(user_id: str, question: str, db_path: str | None = None):
    """Yields ("progress", text) for status updates and a final
    ("answer", text) with the actual response — mirrors
    agent.digest_graph.stream_daily_digest's UX for GET /chat/stream (SSE)."""
    yield ("progress", "Looking into that…")
    graph = await _compile_graph_for_chat(user_id, db_path)
    final_answer = None
    async for update in graph.astream(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": RECURSION_LIMIT},
        stream_mode="updates",
    ):
        node_name, partial = next(iter(update.items()))
        new_messages = partial.get("messages") or []
        if node_name == "react":
            if new_messages:
                last = new_messages[-1]
                final_answer = last.content if isinstance(last.content, str) else str(last.content)
            yield ("progress", "Got an answer — verifying it against the data…")
        elif node_name == "template_fallback":
            if new_messages:
                final_answer = new_messages[-1].content
            yield ("progress", "That didn't check out against the data — using a verified fallback instead.")
    yield ("answer", final_answer or "I couldn't produce an answer.")
