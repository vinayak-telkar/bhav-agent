"""
Chat graph tests: real MCP tools (subprocess -> DAL -> seeded DB) + a
scripted fake BaseChatModel, so the graph's tool-calling loop, read-only
allowlist, and grounding guardrail are exercised without needing a live
Groq/Cerebras API key. Unlike digest's fake (a single structured-output
call), create_react_agent drives a real multi-turn tool-calling loop, so the
fake model here inspects message history dynamically (mirroring how a real
model would use tool results) rather than returning a fixed response list.
"""
import json
import sys
from pathlib import Path
from typing import Callable

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.chat_graph import READ_ONLY_TOOLS, build_chat_graph

BACKEND_DIR = Path(__file__).resolve().parent.parent
SERVER_PATH = BACKEND_DIR / "mcp_server" / "server.py"


class ScriptedToolCallingModel(BaseChatModel):
    """First turn: requests a fixed tool call. Every subsequent turn: builds
    a final answer from whatever ToolMessage content is now in history via
    build_final_answer — lets tests construct grounded/ungrounded responses
    using real tool output instead of guessing values ahead of time."""

    tool_name: str
    tool_args: dict
    build_final_answer: Callable[[list[ToolMessage]], str]
    model_config = {"arbitrary_types_allowed": True}

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_messages:
            ai = AIMessage(
                content="", tool_calls=[{"name": self.tool_name, "args": self.tool_args, "id": "call_1"}]
            )
        else:
            ai = AIMessage(content=self.build_final_answer(tool_messages))
        return ChatResult(generations=[ChatGeneration(message=ai)])


def _tool_json(tool_message: ToolMessage) -> dict:
    # build_chat_graph wraps every tool to return a plain JSON string (see
    # agent.chat_graph._wrap_as_string_tool) — no content-block unwrapping needed here.
    return json.loads(tool_message.content)


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "chat_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


@pytest.fixture
async def all_tools(seeded_db_path):
    client = MultiServerMCPClient(
        {
            "bhav": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(SERVER_PATH)],
                "env": {"DATABASE_PATH": seeded_db_path},
                "cwd": str(BACKEND_DIR),
            }
        }
    )
    return await client.get_tools()


@pytest.fixture
def read_only_tools(all_tools):
    return [t for t in all_tools if t.name in READ_ONLY_TOOLS]


async def test_grounded_answer_passes_through_unchanged(read_only_tools):
    def build_answer(tool_messages):
        data = _tool_json(tool_messages[-1])
        return f"Delivery is {data['deliv_pct']}% vs a {data['deliv_pct_20d_avg']}% baseline."

    llm = ScriptedToolCallingModel(
        tool_name="get_stock_snapshot", tool_args={"symbol": "DEMOACCUM"}, build_final_answer=build_answer
    )
    graph = build_chat_graph(read_only_tools, llm, "demo-user-0001")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "why DEMOACCUM?"}]}, config={"recursion_limit": 6}
    )
    assert result["grounded"] is True
    assert "65.19" in result["messages"][-1].content


async def test_fabricated_number_routes_to_template_fallback(read_only_tools):
    llm = ScriptedToolCallingModel(
        tool_name="get_stock_snapshot",
        tool_args={"symbol": "DEMOACCUM"},
        build_final_answer=lambda _tool_messages: "Delivery is 9999.9%, a figure invented by this fake model.",
    )
    graph = build_chat_graph(read_only_tools, llm, "demo-user-0001")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "why DEMOACCUM?"}]}, config={"recursion_limit": 6}
    )
    assert result["grounded"] is False
    final_text = result["messages"][-1].content
    assert "9999.9" not in final_text
    assert "raw data instead" in final_text


@pytest.mark.parametrize(
    "leaked_text",
    [
        # Trailing leak on otherwise-fine prose.
        "I'm not able to create or share jokes, but I can help explain your "
        'portfolio. <function=get_portfolio>{"user_id": "demo-user-0001"}</function>',
        # Leaked syntax referenced inline as a sentence's own subject — stripping
        # just the tag would leave grammatically broken text ("using the  or
        # functions"), which is exactly why this always falls back rather than
        # trying to salvage partial prose (see verify_grounded's comment).
        'Try the <function=get_portfolio>{}</function> or <function=get_wishlist>{}</function> functions.',
        # Nothing but the leaked tag.
        '<function=get_portfolio>{"user_id": "demo-user-0001"}</function>',
    ],
)
async def test_leaked_tool_call_syntax_always_falls_back(read_only_tools, leaked_text):
    """Confirmed live (2026-08-04): llama-3.3-70b-versatile sometimes emits a
    malformed, literal '<function=...>' tag instead of a real tool call. Any such
    leak routes to the safe template fallback — never shown to the user, in any
    form (see verify_grounded's comment for why stripping-and-keeping was tried
    and reverted)."""
    llm = ScriptedToolCallingModel(
        tool_name="get_stock_snapshot",
        tool_args={"symbol": "DEMOACCUM"},
        build_final_answer=lambda _tool_messages, text=leaked_text: text,
    )
    graph = build_chat_graph(read_only_tools, llm, "demo-user-0001")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "tell me a financial joke"}]}, config={"recursion_limit": 6}
    )
    assert result["grounded"] is False
    final_text = result["messages"][-1].content
    assert "<function=" not in final_text
    assert final_text  # non-empty, safe fallback text


async def test_save_insight_and_ingest_never_in_tool_list(read_only_tools):
    names = {t.name for t in read_only_tools}
    assert "save_insight" not in names
    assert "ingest_local_bhavcopy" not in names


async def test_portfolio_wide_question_can_call_get_portfolio(read_only_tools):
    assert any(t.name == "get_portfolio" for t in read_only_tools)

    def build_answer(tool_messages):
        data = _tool_json(tool_messages[-1])
        symbols = ", ".join(r["symbol"] for r in data) if isinstance(data, list) else str(data)
        return f"Your holdings: {symbols}."

    llm = ScriptedToolCallingModel(
        tool_name="get_portfolio", tool_args={"user_id": "demo-user-0001"}, build_final_answer=build_answer
    )
    graph = build_chat_graph(read_only_tools, llm, "demo-user-0001")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "what do I hold?"}]}, config={"recursion_limit": 6}
    )
    assert "DEMOACCUM" in result["messages"][-1].content
