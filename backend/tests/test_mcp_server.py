"""
End-to-end MCP round-trip tests: real MultiServerMCPClient -> subprocess ->
FastMCP server -> DAL -> seeded SQLite DB. Confirms the tool schemas and
plumbing work, not just the DAL functions in isolation (test_dal.py covers
those). This is the same client pattern digest_graph.py/chat_graph.py use.
"""
import sys
from pathlib import Path

import pytest

from agent.mcp_tools import call_tool
from langchain_mcp_adapters.client import MultiServerMCPClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
SERVER_PATH = BACKEND_DIR / "mcp_server" / "server.py"


@pytest.fixture
async def mcp_tools(seeded_db_path):
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
    tools = await client.get_tools()
    yield {t.name: t for t in tools}


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "mcp_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


async def test_all_five_core_tools_discovered(mcp_tools):
    expected = {
        "get_stock_snapshot", "get_delivery_trend", "get_fo_positioning",
        "get_portfolio", "get_wishlist", "get_prior_insights", "save_insight",
    }
    assert expected.issubset(mcp_tools.keys())


async def test_get_stock_snapshot_round_trip(mcp_tools):
    result = await call_tool(mcp_tools["get_stock_snapshot"], symbol="DEMOACCUM")
    assert "deliv_pct_20d_avg" in result
    assert result["deliv_pct"] > result["deliv_pct_20d_avg"]


async def test_get_stock_snapshot_unknown_symbol_returns_error(mcp_tools):
    result = await call_tool(mcp_tools["get_stock_snapshot"], symbol="NOSUCH")
    assert "error" in result


async def test_get_fo_positioning_round_trip(mcp_tools):
    result = await call_tool(mcp_tools["get_fo_positioning"], symbol="DEMORALLY")
    assert result["buildup_classification"] == "long_buildup"


async def test_get_portfolio_round_trip(mcp_tools):
    result = await call_tool(mcp_tools["get_portfolio"], user_id="demo-user-0001")
    symbols = {r["symbol"] for r in result}
    assert symbols == {"DEMOACCUM", "DEMORALLY", "DEMOBE"}


async def test_save_and_get_prior_insights_round_trip(mcp_tools):
    save_result = await call_tool(
        mcp_tools["save_insight"],
        user_id="demo-user-0001",
        symbol="DEMOACCUM",
        trade_date="2026-07-31",
        signal_type="accumulation",
        action="hold",
        confidence="high",
        narrative="Delivery is 65.19% vs a 52.6% baseline.",
        evidence={"deliv_pct_today": 65.19},
        price_at_insight=3191.59,
    )
    assert save_result["status"] == "saved"

    prior = await call_tool(mcp_tools["get_prior_insights"], user_id="demo-user-0001", symbol="DEMOACCUM")
    assert prior[0]["evidence"]["deliv_pct_today"] == 65.19
