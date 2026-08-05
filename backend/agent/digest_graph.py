"""
Mode 1 — the analyst. LangGraph StateGraph on ChatGroq (default gpt-oss-120b),
Cerebras fallback. fetch_portfolio -> snapshot_symbol -> conditional drill_down ->
compare_prior -> write_insight -> verify_insight_grounded -> save_insight.
See TECH_SPEC.md §6 and specs/07_digest_graph.md.
Exposes: run_daily_digest(user_id) -- called by the scheduler.

Why an explicit StateGraph and not a generic ReAct loop: the "does this symbol
need a deeper look" decision must be structural (visible in the graph, not
left to prompt-following) — see tech spec §6. This implementation processes
the portfolio+wishlist as a queue with a loop-back edge (snapshot_symbol ->
... -> save_insight -> snapshot_symbol) rather than fanning out one subgraph
run per symbol — simpler to read and log for the symbol counts this project
runs at (a handful of held/watched stocks), at the cost of running strictly
sequentially rather than in parallel. Revisit only if a real portfolio's size
makes sequential processing too slow.
"""
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm_factory import build_chat_models  # noqa: E402
from agent.mcp_tools import call_tool  # noqa: E402
from agent.prompts import DIGEST_SYSTEM_PROMPT  # noqa: E402

load_dotenv()

MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"

# "Looks off" thresholds — surface only threshold-crossers (PRD §4's
# portfolio-relative principle), not every stock every day.
DELIV_PCT_DEVIATION_THRESHOLD = 0.15  # 15% relative deviation from baseline
VOLUME_HIGH_RATIO = 1.5
VOLUME_LOW_RATIO = 0.6

DECIMAL_NUMBER_PATTERN = re.compile(r"-?\d+\.\d+")


class InsightOutput(BaseModel):
    signal_type: str = Field(
        description="One of the PRD §4 signal signatures (e.g. 'genuine_accumulation', "
        "'leveraged_rally', 'quiet_distribution', 'short_buildup', 'capped_upside', "
        "'speculative_churn', 'liquidity_deterioration', 'positional_support_leaving'), "
        "or 'no_signal' if nothing crosses a threshold."
    )
    action: str = Field(description="Exactly one of the seven actions, verbatim.")
    confidence: Literal["low", "medium", "high"]
    narrative: str = Field(description="Plain-English explanation citing the evidence numbers.")
    evidence: dict[str, float] = Field(
        description="Flat dict of the specific numbers cited, e.g. "
        '{"deliv_pct_today": 65.2, "deliv_pct_20d_avg": 52.6}. Every value here must '
        "come from the tool data you were given this turn."
    )


class SymbolTask(TypedDict):
    symbol: str
    size_bucket: str | None
    status: Literal["held", "wishlist"]


class DigestState(TypedDict):
    user_id: str
    queue: list[SymbolTask]
    current: SymbolTask | None
    current_snapshot: dict | None
    current_drill_down: dict | None
    current_prior: list[dict] | None
    current_insight: dict | None
    results: list[dict]


def _looks_off(snapshot: dict) -> bool:
    if snapshot.get("corporate_action_flag"):
        return True
    if snapshot.get("series") != "EQ":
        return True

    deliv_pct, deliv_baseline = snapshot.get("deliv_pct"), snapshot.get("deliv_pct_20d_avg")
    if deliv_pct is not None and deliv_baseline:
        if abs(deliv_pct - deliv_baseline) / deliv_baseline > DELIV_PCT_DEVIATION_THRESHOLD:
            return True

    volume, volume_baseline = snapshot.get("volume"), snapshot.get("volume_20d_avg")
    if volume is not None and volume_baseline:
        ratio = volume / volume_baseline
        if ratio > VOLUME_HIGH_RATIO or ratio < VOLUME_LOW_RATIO:
            return True

    return False


def _flatten_numbers(data) -> set[float]:
    """Every numeric leaf in a nested dict/list, rounded for tolerant matching."""
    values: set[float] = set()

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            values.add(round(float(node), 1))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return values


_SNAPSHOT_PAIRS = [
    ("close", "prev_close"),
    ("volume", "volume_20d_avg"),
    ("deliv_pct", "deliv_pct_20d_avg"),
]


def _derived_percent_changes(snapshot: dict) -> set[float]:
    """The model naturally states a price/volume/delivery % change in its
    narrative or evidence ("up 2.1%", 'price_change_pct': -1.78) — a
    legitimate, correctly-computable figure, not a fabrication. But it never
    appears as a literal field in any tool's raw output, so the "every cited
    number must match a raw tool number" check was discarding almost every
    real insight over this alone. Confirmed live (2026-08-05): in one full
    digest run, every single grounding failure cited a correctly-computed
    price_change_pct with nothing in the reference set to match against.
    Computes the standard percent-change pairs and includes both signed and
    unsigned (prose often states magnitude only — "down 1.25%" carries no
    literal minus sign) forms."""
    derived: set[float] = set()
    for value_key, baseline_key in _SNAPSHOT_PAIRS:
        value, baseline = snapshot.get(value_key), snapshot.get(baseline_key)
        if value is not None and baseline:
            pct_change = round((value - baseline) / baseline * 100, 1)
            derived.add(pct_change)
            derived.add(abs(pct_change))
    return derived


def _derived_point_differences(snapshot: dict) -> set[float]:
    """Alongside percent changes, models sometimes state the raw point/rupee
    difference itself ("down 22.5 points", "up 19.68 points of delivery %")
    — again a real, correctly-computed figure with no literal counterpart in
    tool output. Confirmed live (2026-08-05): CELLO ('19.68' = deliv_pct
    64.08 - baseline 44.4) and PIDILITIND ('22.5' = prev_close 1642.5 -
    close 1620.0) insights were discarded over exactly this."""
    derived: set[float] = set()
    for value_key, baseline_key in _SNAPSHOT_PAIRS:
        value, baseline = snapshot.get(value_key), snapshot.get(baseline_key)
        if value is not None and baseline is not None:
            diff = round(value - baseline, 2)
            derived.add(diff)
            derived.add(abs(diff))
    return derived


def _derived_ratios(snapshot: dict) -> set[float]:
    """Models occasionally express a comparison as a multiple rather than a
    percent ("volume was 2.66x its 20-day average"). Same pattern as the
    percent/point-difference derivations above — a real computation, not a
    fabrication."""
    derived: set[float] = set()
    for value_key, baseline_key in _SNAPSHOT_PAIRS:
        value, baseline = snapshot.get(value_key), snapshot.get(baseline_key)
        if value is not None and baseline:
            ratio = value / baseline
            derived.add(round(ratio, 1))
            derived.add(round(ratio, 2))
    return derived


def _scaled_millions(reference_numbers: set[float]) -> set[float]:
    """Models often abbreviate large counts (volume, delivery quantity,
    futures OI change) in narrative prose as e.g. "52.7M" or "6.11M" — a
    legitimate unit-scaled restatement of a real tool number, not a
    fabrication, but it doesn't literally match the raw integer in any
    tool's output. Confirmed live (2026-08-05): LICI/PAYTM/RELIANCE/TVSMOTOR
    insights were discarded over exactly this. Applied to the full reference
    set (snapshot + drill-down + prior insights) rather than specific field
    names, since any large raw count is a candidate for abbreviation."""
    scaled: set[float] = set()
    for value in reference_numbers:
        if abs(value) >= 100_000:
            scaled.add(round(value / 1_000_000, 1))
            scaled.add(round(value / 1_000_000, 2))
    return scaled


def _is_grounded(insight: InsightOutput, reference_numbers: set[float]) -> bool:
    """Every number in the insight's evidence dict and narrative (decimal
    numbers only — integer counts like '20-day' or '7 actions' are structural
    language, not cited figures) must trace back to this turn's tool output."""
    candidates = list(_flatten_numbers(insight.evidence))
    candidates += [float(m) for m in DECIMAL_NUMBER_PATTERN.findall(insight.narrative)]

    for value in candidates:
        # + 1e-9 absorbs float-arithmetic noise at the tolerance boundary — e.g. a
        # narrative citing "1.25%" against a derived reference rounded to 1.2 has a
        # true difference of exactly 0.05, but abs(1.25 - 1.2) computes as
        # 0.050000000000000044 in float64, tripping <= 0.05 by a sliver. Confirmed
        # live (2026-08-05): this discarded a correctly-grounded ASIANPAINT insight.
        if not any(abs(value - ref) <= max(0.05, abs(ref) * 0.01) + 1e-9 for ref in reference_numbers):
            return False
    return True


def _template_insight(snapshot: dict) -> InsightOutput:
    """Deterministic, tool-data-only fallback when the LLM's synthesis fails
    grounding — no prose invention, just a restatement of the snapshot."""
    evidence = {
        k: v
        for k, v in {
            "close": snapshot.get("close"),
            "deliv_pct": snapshot.get("deliv_pct"),
            "deliv_pct_20d_avg": snapshot.get("deliv_pct_20d_avg"),
            "volume": snapshot.get("volume"),
            "volume_20d_avg": snapshot.get("volume_20d_avg"),
        }.items()
        if v is not None
    }
    return InsightOutput(
        signal_type="ungrounded_fallback",
        action="Review the position",
        confidence="low",
        narrative=(
            "The model's synthesis for this symbol could not be verified against tool "
            "data, so this is an automated summary rather than a generated read: "
            f"close {snapshot.get('close')}, delivery% {snapshot.get('deliv_pct')} vs "
            f"{snapshot.get('deliv_pct_20d_avg')} baseline. Recommend a manual review."
        ),
        evidence=evidence,
    )


def build_digest_graph(tools: dict[str, BaseTool], llm: BaseChatModel):
    # Groq's free tier enforces a per-organization tokens-per-minute cap (observed:
    # 8000 TPM for gpt-oss-120b) shared across every symbol processed in one digest
    # run — processing 5 symbols back to back can exceed it well before the
    # Groq<->Cerebras provider-level fallback (run_daily_digest) even matters, since
    # that only kicks in if a Cerebras key is configured. Retrying the same call
    # with backoff handles this correctly on its own: TPM limits reset every
    # minute, so waiting out Groq's suggested delay and retrying succeeds without
    # needing a second provider at all.
    structured_llm = llm.with_structured_output(InsightOutput).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )

    async def fetch_portfolio(state: DigestState) -> dict:
        held = await call_tool(tools["get_portfolio"], user_id=state["user_id"])
        wishlist = await call_tool(tools["get_wishlist"], user_id=state["user_id"])
        queue: list[SymbolTask] = [
            {"symbol": r["symbol"], "size_bucket": r.get("size_bucket"), "status": "held"} for r in held
        ] + [{"symbol": r["symbol"], "size_bucket": None, "status": "wishlist"} for r in wishlist]
        return {"queue": queue, "results": []}

    async def snapshot_symbol(state: DigestState) -> dict:
        current, *remaining = state["queue"]
        snapshot = await call_tool(tools["get_stock_snapshot"], symbol=current["symbol"])
        return {"queue": remaining, "current": current, "current_snapshot": snapshot}

    async def drill_down(state: DigestState) -> dict:
        symbol = state["current"]["symbol"]
        trend = await call_tool(tools["get_delivery_trend"], symbol=symbol, days=20)
        fo_positioning = await call_tool(tools["get_fo_positioning"], symbol=symbol)
        return {"current_drill_down": {"delivery_trend": trend, "fo_positioning": fo_positioning}}

    async def compare_prior(state: DigestState) -> dict:
        prior = await call_tool(
            tools["get_prior_insights"], user_id=state["user_id"], symbol=state["current"]["symbol"]
        )
        return {"current_prior": prior}

    async def write_insight(state: DigestState) -> dict:
        context = {
            "symbol": state["current"]["symbol"],
            "size_bucket": state["current"]["size_bucket"],
            "status": state["current"]["status"],
            "snapshot": state["current_snapshot"],
            "drill_down": state.get("current_drill_down"),
            "prior_insights": state.get("current_prior") or [],
        }
        insight = await structured_llm.ainvoke(
            [
                {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
                {"role": "user", "content": _format_context(context)},
            ]
        )
        return {"current_insight": insight.model_dump()}

    async def verify_insight_grounded(state: DigestState) -> dict:
        reference = _flatten_numbers(state["current_snapshot"])
        reference |= _derived_percent_changes(state["current_snapshot"])
        reference |= _derived_point_differences(state["current_snapshot"])
        reference |= _derived_ratios(state["current_snapshot"])
        if state.get("current_drill_down"):
            reference |= _flatten_numbers(state["current_drill_down"])
        if state.get("current_prior"):
            reference |= _flatten_numbers([p["evidence"] for p in state["current_prior"]])
        # Scaled after every other source is unioned in, so million-abbreviations of
        # drill-down numbers (e.g. futures OI change) are covered too, not just snapshot.
        reference |= _scaled_millions(reference)

        insight = InsightOutput(**state["current_insight"])
        grounded = _is_grounded(insight, reference)
        if not grounded:
            # The discarded narrative/evidence were never persisted anywhere (by
            # design — an ungrounded number shouldn't reach the DB or the user),
            # which made "why did this fall back?" undebuggable after the fact.
            # Log it here instead: this is the one place that sees both the
            # model's original (rejected) output and the reference numbers it
            # was checked against.
            logger.warning(
                "Ungrounded insight discarded for %s (using template_fallback instead): "
                "model=%s action=%s confidence=%s narrative=%r evidence=%r | "
                "reference numbers this turn: %s",
                state["current"]["symbol"],
                insight.signal_type,
                insight.action,
                insight.confidence,
                insight.narrative,
                insight.evidence,
                sorted(reference),
            )
        return {"current_insight": {**state["current_insight"], "_grounded": grounded}}

    async def template_fallback(state: DigestState) -> dict:
        insight = _template_insight(state["current_snapshot"])
        return {"current_insight": {**insight.model_dump(), "_grounded": True}}

    async def save_insight_node(state: DigestState) -> dict:
        insight = state["current_insight"]
        saved = await call_tool(
            tools["save_insight"],
            user_id=state["user_id"],
            symbol=state["current"]["symbol"],
            trade_date=state["current_snapshot"]["trade_date"],
            signal_type=insight["signal_type"],
            action=insight["action"],
            confidence=insight["confidence"],
            narrative=insight["narrative"],
            evidence=insight["evidence"],
            price_at_insight=state["current_snapshot"]["close"],
        )
        result = {
            "symbol": state["current"]["symbol"],
            "insight_id": saved["id"],
            "signal_type": insight["signal_type"],
            "action": insight["action"],
            "confidence": insight["confidence"],
            "narrative": insight["narrative"],
            "grounded": insight["_grounded"],
        }
        return {"results": state["results"] + [result], "current": None, "current_snapshot": None,
                "current_drill_down": None, "current_prior": None, "current_insight": None}

    async def skip_no_data(state: DigestState) -> dict:
        return {"current": None, "current_snapshot": None}

    def route_after_fetch(state: DigestState) -> str:
        return "next" if state["queue"] else "done"

    def route_after_snapshot(state: DigestState) -> str:
        snapshot = state["current_snapshot"]
        if snapshot.get("error"):
            return "no_data"
        return "drill_down" if _looks_off(snapshot) else "compare_prior"

    def route_after_verify(state: DigestState) -> str:
        return "pass" if state["current_insight"]["_grounded"] else "fail"

    graph = StateGraph(DigestState)
    graph.add_node("fetch_portfolio", fetch_portfolio)
    graph.add_node("snapshot_symbol", snapshot_symbol)
    graph.add_node("drill_down", drill_down)
    graph.add_node("compare_prior", compare_prior)
    graph.add_node("write_insight", write_insight)
    graph.add_node("verify_insight_grounded", verify_insight_grounded)
    graph.add_node("template_fallback", template_fallback)
    graph.add_node("save_insight", save_insight_node)
    graph.add_node("skip_no_data", skip_no_data)

    graph.set_entry_point("fetch_portfolio")
    graph.add_conditional_edges("fetch_portfolio", route_after_fetch, {"next": "snapshot_symbol", "done": END})
    graph.add_conditional_edges(
        "snapshot_symbol", route_after_snapshot,
        {"drill_down": "drill_down", "compare_prior": "compare_prior", "no_data": "skip_no_data"},
    )
    graph.add_edge("drill_down", "compare_prior")
    graph.add_edge("compare_prior", "write_insight")
    graph.add_edge("write_insight", "verify_insight_grounded")
    graph.add_conditional_edges(
        "verify_insight_grounded", route_after_verify, {"pass": "save_insight", "fail": "template_fallback"}
    )
    graph.add_edge("template_fallback", "save_insight")
    graph.add_conditional_edges("save_insight", route_after_fetch, {"next": "snapshot_symbol", "done": END})
    graph.add_conditional_edges("skip_no_data", route_after_fetch, {"next": "snapshot_symbol", "done": END})

    return graph.compile()


def _format_context(context: dict) -> str:
    import json

    return json.dumps(context, indent=2, default=str)


async def _compile_graph_for_run(db_path: str | None = None):
    """Shared setup for both run_daily_digest (blocking) and
    stream_daily_digest (progress-streaming) — one place that builds the MCP
    connection and model pair, so the two entry points can't drift apart."""
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
    tools = {t.name: t for t in await client.get_tools()}
    # NOT the tech spec's original ~16000 estimate — that requests more tokens
    # than a single call's TPM budget on Groq's free tier for gpt-oss-120b
    # (observed limit: 8000 TPM per request, not just cumulative; a 16000
    # max_tokens request is rejected outright with a 413, every time, not
    # just under load). write_insight's actual output is one small
    # InsightOutput object (a short narrative + a handful of evidence
    # numbers) — 2000 is generous headroom for that, not a tight squeeze.
    primary, fallback = build_chat_models("DIGEST", max_tokens=2000)
    llm = primary.with_fallbacks([fallback]) if fallback else primary
    return build_digest_graph(tools, llm)


async def run_daily_digest(user_id: str, db_path: str | None = None) -> list[dict]:
    """Compiles and runs the digest graph for one user. Called by the
    scheduler (app/main.py wires APScheduler to this)."""
    graph = await _compile_graph_for_run(db_path)
    # Generous but bounded — a handful of symbols x ~6 hops each; caps a
    # runaway loop rather than letting a free-tier model spin indefinitely
    # (tech spec §6's "same reasoning as chat's cap" note).
    final_state = await graph.ainvoke({"user_id": user_id}, config={"recursion_limit": 100})
    return final_state["results"]


def _progress_message(node_name: str, state: dict) -> str | None:
    """Translates a graph step into a human-readable status line — what the
    user actually wants to see while waiting (PRD/user feedback: not raw MCP
    'ListToolsRequest'/'CallToolRequest' log noise, actual step-by-step work).
    Returns None for steps that don't need their own line."""
    current = state.get("current")
    symbol = current["symbol"] if current else None

    if node_name == "fetch_portfolio":
        return f"Found {len(state.get('queue', []))} symbols to check (held + wishlist)."
    if node_name == "snapshot_symbol":
        return f"Checking {symbol}…"
    if node_name == "drill_down":
        return f"{symbol} looks unusual — pulling delivery trend and F&O positioning…"
    if node_name == "compare_prior":
        return f"Comparing {symbol} against prior insights…"
    if node_name == "write_insight":
        return f"Writing an insight for {symbol}…"
    if node_name == "verify_insight_grounded":
        insight = state.get("current_insight") or {}
        return (
            f"Verified {symbol}'s insight against the data — looks good."
            if insight.get("_grounded")
            else f"{symbol}'s draft insight didn't hold up against the data — using a safe fallback instead."
        )
    if node_name == "save_insight":
        results = state.get("results") or []
        if results:
            last = results[-1]
            return f"Saved {last['symbol']}: {last['action']} ({last['confidence']}) — {len(results)} done so far."
        return None
    if node_name == "skip_no_data":
        return f"No data available for {symbol} — skipping."
    return None


async def stream_daily_digest(user_id: str, db_path: str | None = None):
    """Same run as run_daily_digest, but yields a human-readable progress
    string after each graph step instead of returning only the final result
    — powers the dashboard's live "what is the agent doing" view (POST
    /digest/run-stream) instead of a blank spinner for however long a
    multi-symbol run takes."""
    graph = await _compile_graph_for_run(db_path)
    state: dict = {"user_id": user_id}
    async for update in graph.astream({"user_id": user_id}, config={"recursion_limit": 100}, stream_mode="updates"):
        node_name, partial = next(iter(update.items()))
        state.update(partial)
        message = _progress_message(node_name, state)
        if message:
            yield message


if __name__ == "__main__":
    user_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEMO_USER_ID", "demo-user-0001")
    results = asyncio.run(run_daily_digest(user_id))
    for r in results:
        print(f"{r['symbol']}: {r['action']} ({r['confidence']}) — {r['narrative']}")
