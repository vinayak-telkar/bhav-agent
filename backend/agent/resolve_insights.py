"""
Deterministic insight status resolution — no LLM involved, same "tools
compute all numbers" philosophy as the digest's MCP tools. Compares each
pending/strengthening insight's price_at_insight against the symbol's latest
close and how many trading sessions have elapsed since the insight was made,
against signal_type's expected price direction (PRD §4's signal->action
table). See specs/11_insight_tracker_resolution.md for the full contract —
explicitly not the 6-month backtest (bulk historical data this project has
no practical way to acquire; see that spec's Purpose).
"""
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import bars as bars_dal  # noqa: E402
from app.data import db as db_module  # noqa: E402
from app.data import insights as insights_dal  # noqa: E402

HORIZON_SESSIONS = 5  # PRD §4: "Review; resolve over ~5 sessions"
CONFIRM_THRESHOLD_PCT = 3.0
STRENGTHEN_THRESHOLD_PCT = 1.5

Direction = Literal["up", "down", "flat"]

# PRD §4's signal -> insight -> action table, read as a directional
# expectation: does the signature predict the price should rise, fall, or
# stay roughly flat. None = no directional claim was made (nothing to
# confirm/strengthen against, but still expires once stale).
SIGNAL_EXPECTED_DIRECTION: dict[str, Direction | None] = {
    "genuine_accumulation": "up",
    "leveraged_rally": "down",
    "quiet_distribution": "down",
    "short_buildup": "down",
    "capped_upside": "flat",
    "speculative_churn": "flat",
    "liquidity_deterioration": "down",
    "positional_support_leaving": "down",
    "no_signal": None,
    "ungrounded_fallback": None,
}


def _sessions_elapsed(conn, symbol: str, since_trade_date: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM daily_bars WHERE symbol = ? AND trade_date > ?",
        (symbol, since_trade_date),
    ).fetchone()
    return row["n"]


def _moved_as_expected(direction: Direction, outcome_pct: float) -> bool:
    if direction == "up":
        return outcome_pct >= CONFIRM_THRESHOLD_PCT
    if direction == "down":
        return outcome_pct <= -CONFIRM_THRESHOLD_PCT
    return abs(outcome_pct) < CONFIRM_THRESHOLD_PCT  # flat


def _classify(direction: Direction | None, outcome_pct: float, sessions_elapsed: int) -> str | None:
    """Returns the new status, or None if nothing should change yet."""
    if direction is None:
        return "expired" if sessions_elapsed >= HORIZON_SESSIONS else None

    if sessions_elapsed >= HORIZON_SESSIONS:
        return "confirmed" if _moved_as_expected(direction, outcome_pct) else "expired"

    if direction == "flat":
        return None  # nothing to "strengthen" early for a flat expectation
    early_signal = (
        outcome_pct >= STRENGTHEN_THRESHOLD_PCT if direction == "up" else outcome_pct <= -STRENGTHEN_THRESHOLD_PCT
    )
    return "strengthening" if early_signal else None


def _check_one(conn, insight: dict) -> dict | None:
    """Re-checks a single insight. Returns an updated summary dict if
    anything was written, else None (skipped — no new data yet)."""
    symbol = insight["symbol"]
    sessions_elapsed = _sessions_elapsed(conn, symbol, insight["trade_date"])
    if sessions_elapsed == 0:
        return None

    snapshot = bars_dal.get_stock_snapshot(conn, symbol)
    if snapshot is None:
        return None

    outcome_pct = round(
        (snapshot["close"] - insight["price_at_insight"]) / insight["price_at_insight"] * 100, 2
    )
    direction = SIGNAL_EXPECTED_DIRECTION.get(insight["signal_type"])
    new_status = _classify(direction, outcome_pct, sessions_elapsed)

    if new_status is None:
        # Still record the live outcome_pct even when status doesn't change
        # yet, so the tracker shows movement toward/away from confirmation.
        insights_dal.update_insight_status(
            conn, insight["id"], status=insight["status"], outcome_pct=outcome_pct, resolved=False
        )
        return None

    resolved = new_status in ("confirmed", "expired")
    insights_dal.update_insight_status(conn, insight["id"], status=new_status, outcome_pct=outcome_pct, resolved=resolved)
    return {**insight, "status": new_status, "outcome_pct": outcome_pct}


def resolve_insights(user_id: str, db_path: str | None = None) -> list[dict]:
    """Returns the insights whose status actually changed this run."""
    conn = db_module.get_connection(db_path)
    try:
        candidates = insights_dal.get_unresolved_insights(conn, user_id)
        updated = []
        for insight in candidates:
            result = _check_one(conn, insight)
            if result:
                updated.append(result)
        return updated
    finally:
        conn.close()


def stream_resolve_insights(user_id: str, db_path: str | None = None) -> Iterator[str]:
    """Same resolution, yielding a human-readable progress line per insight
    checked — mirrors agent.digest_graph.stream_daily_digest's UX. A plain
    sync generator (no LangGraph involved, there's no graph here)."""
    conn = db_module.get_connection(db_path)
    try:
        candidates = insights_dal.get_unresolved_insights(conn, user_id)
        if not candidates:
            yield "No prior insights waiting to be re-checked."
            return
        yield f"Re-checking {len(candidates)} prior insight(s) against fresh price data…"
        for insight in candidates:
            result = _check_one(conn, insight)
            if result is None:
                continue
            yield f"{result['symbol']}: {insight['status']} → {result['status']} ({result['outcome_pct']:+.2f}%)"
    finally:
        conn.close()
