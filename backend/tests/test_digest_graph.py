"""
Digest graph tests: real MCP tools (subprocess -> DAL -> seeded DB) + a fake
LLM double, so the graph's routing/grounding/fallback logic is exercised
without needing a live Groq/Cerebras API key (unavailable in this build
environment — see specs/02's Changelog).
"""
import json
import sys
from pathlib import Path

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.digest_graph import (
    InsightOutput,
    _derived_percent_changes,
    _derived_point_differences,
    _derived_ratios,
    _flatten_numbers,
    _is_grounded,
    _progress_message,
    _scaled_millions,
    build_digest_graph,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
SERVER_PATH = BACKEND_DIR / "mcp_server" / "server.py"


class _FakeStructuredRunnable:
    def __init__(self, responder):
        self._responder = responder

    async def ainvoke(self, messages):
        context = json.loads(messages[1]["content"])
        return self._responder(context)

    def with_retry(self, **kwargs):
        # real code chains .with_retry() onto the structured runnable (digest_graph.py) —
        # a no-op here since this fake never needs to actually retry
        return self


class _FakeChatModel:
    def __init__(self, responder):
        self._responder = responder

    def with_structured_output(self, schema):
        return _FakeStructuredRunnable(self._responder)


def _grounded_responder(context: dict) -> InsightOutput:
    snapshot = context["snapshot"]
    return InsightOutput(
        signal_type="genuine_accumulation",
        action="Hold, no action",
        confidence="high",
        narrative=f"Delivery is {snapshot['deliv_pct']}% vs a {snapshot['deliv_pct_20d_avg']}% baseline.",
        evidence={
            "deliv_pct_today": snapshot["deliv_pct"],
            "deliv_pct_20d_avg": snapshot["deliv_pct_20d_avg"],
        },
    )


def _ungrounded_responder(context: dict) -> InsightOutput:
    return InsightOutput(
        signal_type="genuine_accumulation",
        action="Hold, no action",
        confidence="high",
        narrative="Delivery is 9999.9% vs baseline, a figure invented by this fake model.",
        evidence={"deliv_pct_today": 9999.9},
    )


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "digest_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


@pytest.fixture
async def tools(seeded_db_path):
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
    tool_list = await client.get_tools()
    return {t.name: t for t in tool_list}


async def test_digest_processes_full_portfolio_with_grounded_insights(tools):
    graph = build_digest_graph(tools, _FakeChatModel(_grounded_responder))
    final_state = await graph.ainvoke({"user_id": "demo-user-0001"}, config={"recursion_limit": 100})

    results = final_state["results"]
    processed_symbols = {r["symbol"] for r in results}
    # held (DEMOACCUM, DEMORALLY, DEMOBE) + wishlist (DEMODIST, DEMOSMALL)
    assert processed_symbols == {"DEMOACCUM", "DEMORALLY", "DEMOBE", "DEMODIST", "DEMOSMALL"}
    assert all(r["grounded"] for r in results)
    assert all(r["signal_type"] == "genuine_accumulation" for r in results)


async def test_digest_falls_back_to_template_when_llm_fabricates_a_number(tools):
    graph = build_digest_graph(tools, _FakeChatModel(_ungrounded_responder))
    final_state = await graph.ainvoke({"user_id": "demo-user-0001"}, config={"recursion_limit": 100})

    results = final_state["results"]
    assert len(results) == 5
    for r in results:
        assert r["signal_type"] == "ungrounded_fallback"
        assert r["grounded"] is True  # template output is grounded by construction


def test_derived_percent_changes_matches_real_model_citation():
    """Confirmed live (2026-08-05): a full digest run discarded almost every
    insight because the model correctly cited a computed price-change
    percentage (e.g. 'up 2.1%', evidence={'price_change_pct': 2.1}) that
    never appeared as a literal field in any tool's raw output — a real
    grounding-check bug, not model unreliability. Real numbers from that
    run: AARTIPHARM closed 690.0 vs prev_close 675.8, model cited 2.1."""
    snapshot = {"close": 690.0, "prev_close": 675.8}
    assert 2.1 in _derived_percent_changes(snapshot)


def test_insight_citing_correct_price_change_pct_is_now_grounded():
    """The exact failure shape from the live run: evidence includes a
    correctly-computed price_change_pct alongside real snapshot numbers —
    this must pass grounding now, not fall back to the template."""
    snapshot = {
        "close": 690.0,
        "prev_close": 675.8,
        "deliv_pct": 50.7,
        "deliv_pct_20d_avg": 47.97,
        "volume": 199670.0,
        "volume_20d_avg": 155910.0,
    }
    insight = InsightOutput(
        signal_type="genuine_accumulation",
        action="Hold, no action",
        confidence="medium",
        narrative="The stock closed at 690, up ~2.1% from the prior close of 675.8.",
        evidence={
            "close": 690.0,
            "prev_close": 675.8,
            "deliv_pct_today": 50.7,
            "deliv_pct_20d_avg": 47.97,
            "price_change_pct": 2.1,
        },
    )
    reference = {round(v, 1) for v in [690.0, 675.8, 50.7, 47.97, 199670.0, 155910.0]}
    reference |= _derived_percent_changes(snapshot)
    assert _is_grounded(insight, reference)


def test_narrative_pct_at_tolerance_boundary_is_grounded_despite_float_noise():
    """Confirmed live (2026-08-05): ASIANPAINT still fell back after the
    _derived_percent_changes fix. Root cause: the narrative cites the raw,
    unrounded price-change ('about 1.25% lower') while the derived reference
    is rounded to 1 decimal (1.2) — true difference is exactly the 0.05
    tolerance boundary, but abs(1.25 - 1.2) evaluates to 0.050000000000000044
    in float64, tripping the '<= 0.05' check by a sliver. The evidence dict's
    own price_change_pct (-1.245) passed fine because _flatten_numbers rounds
    it to -1.2 first — only the narrative's full-precision citation failed."""
    snapshot = {"close": 2775.0, "prev_close": 2810.0}
    insight = InsightOutput(
        signal_type="no_signal",
        action="Hold, no action",
        confidence="high",
        narrative="Today's close 2775.0 is about 1.25% lower than yesterday's 2810.0.",
        evidence={"close": 2775.0, "prev_close": 2810.0, "price_change_pct": -1.245},
    )
    reference = {round(v, 1) for v in [2775.0, 2810.0]}
    reference |= _derived_percent_changes(snapshot)
    assert _is_grounded(insight, reference)


def _full_reference(snapshot: dict, drill_down: dict | None = None) -> set[float]:
    """Mirrors verify_insight_grounded's reference-building steps, for tests
    that exercise the point-difference/ratio/millions-scaling additions."""
    reference = _flatten_numbers(snapshot)
    reference |= _derived_percent_changes(snapshot)
    reference |= _derived_point_differences(snapshot)
    reference |= _derived_ratios(snapshot)
    if drill_down:
        reference |= _flatten_numbers(drill_down)
    reference |= _scaled_millions(reference)
    return reference


def test_narrative_point_difference_is_grounded():
    """Confirmed live (2026-08-05): CELLO's insight was discarded for citing
    '19.68 points' (deliv_pct 64.08 - baseline 44.4), a correct raw point
    difference with no literal or percent-derived counterpart."""
    snapshot = {"deliv_pct": 64.08, "deliv_pct_20d_avg": 44.4}
    insight = InsightOutput(
        signal_type="quiet_distribution",
        action="Review the position",
        confidence="high",
        narrative="Delivery percentage jumped to 64.08%, up 19.68 points from the 20-day average of 44.4%.",
        evidence={"deliv_pct_today": 64.08, "deliv_pct_20d_avg": 44.4},
    )
    assert _is_grounded(insight, _full_reference(snapshot))


def test_narrative_abbreviated_millions_is_grounded():
    """Confirmed live (2026-08-05): LICI's insight was discarded for citing
    '52.7M' / '1.3M' (volume 52,718,651 / baseline 1,299,272 abbreviated to
    millions), a correct restatement of raw tool numbers."""
    snapshot = {"volume": 52718651.0, "volume_20d_avg": 1299272.0}
    insight = InsightOutput(
        signal_type="short_buildup",
        action="Review the position",
        confidence="medium",
        narrative="Volume spiked to 52.7M versus a 1.3M average.",
        evidence={"volume_today": 52718651.0, "volume_20d_avg": 1299272.0},
    )
    assert _is_grounded(insight, _full_reference(snapshot))


def test_narrative_abbreviated_millions_covers_drill_down_numbers():
    """Same abbreviation pattern (see test above) but for a drill-down-only
    field (futures OI change), confirming the scaling is applied to the
    full reference set, not just the snapshot."""
    snapshot = {"close": 391.3, "prev_close": 428.5}
    drill_down = {"fo_positioning": {"fut_oi_change": 48123600.0}}
    insight = InsightOutput(
        signal_type="short_buildup",
        action="Review the position",
        confidence="medium",
        narrative="Futures open interest jumped by 48.1M contracts.",
        evidence={"fut_oi_change": 48123600.0},
    )
    assert _is_grounded(insight, _full_reference(snapshot, drill_down))


def test_narrative_ratio_is_grounded():
    """Confirmed live (2026-08-05): PAYTM's insight was discarded for citing
    '2.66x' (volume 6,114,443 / baseline 2,303,054 ~= 2.65), a correct ratio
    with no literal or percent-derived counterpart."""
    snapshot = {"volume": 6114443.0, "volume_20d_avg": 2303054.0}
    insight = InsightOutput(
        signal_type="speculative_churn",
        action="Tighten exit discipline",
        confidence="high",
        narrative="PAYTM traded at 6.11M shares, about 2.66x its 20-day average of 2.30M.",
        evidence={"volume_today": 6114443.0, "volume_20d_avg": 2303054.0},
    )
    assert _is_grounded(insight, _full_reference(snapshot))


async def test_reliance_and_tcs_route_through_drill_down(tools):
    """DEMOACCUM (accumulation, deliv% ~65 vs baseline ~52) and DEMORALLY (leveraged
    rally, deliv% falling sharply) should both cross the 'looks off' delivery
    deviation threshold and trigger drill_down; confirm via the graph's own
    routing rather than re-deriving the numbers by hand here."""
    from agent.digest_graph import _looks_off
    from agent.mcp_tools import call_tool

    reliance_snapshot = await call_tool(tools["get_stock_snapshot"], symbol="DEMOACCUM")
    tcs_snapshot = await call_tool(tools["get_stock_snapshot"], symbol="DEMORALLY")
    assert _looks_off(reliance_snapshot)
    assert _looks_off(tcs_snapshot)


async def test_stream_yields_human_readable_progress_for_every_symbol(tools):
    """The streaming path (agent.digest_graph.stream_daily_digest, powering
    the dashboard's live progress view) must yield readable step-by-step
    status lines, not raw MCP/tool-call noise — this exercises the same
    graph.astream() + _progress_message() plumbing stream_daily_digest uses,
    without needing a live Groq key (build_digest_graph is called directly,
    same as the other tests in this file)."""
    graph = build_digest_graph(tools, _FakeChatModel(_grounded_responder))

    state: dict = {"user_id": "demo-user-0001"}
    messages: list[str] = []
    async for update in graph.astream({"user_id": "demo-user-0001"}, config={"recursion_limit": 100}, stream_mode="updates"):
        node_name, partial = next(iter(update.items()))
        state.update(partial)
        message = _progress_message(node_name, state)
        if message:
            messages.append(message)

    assert any("Found 5 symbols" in m for m in messages)
    assert any("Checking DEMOACCUM" in m for m in messages)
    assert any(m.startswith("Saved DEMOACCUM:") for m in messages)
    saved_lines = [m for m in messages if m.startswith("Saved ")]
    assert len(saved_lines) == 5  # one per held+wishlist symbol
    assert all("Processing request" not in m for m in messages)  # no raw MCP log noise
