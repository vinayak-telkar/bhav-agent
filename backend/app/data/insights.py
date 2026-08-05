"""
insights DAL — cross-day agent memory (get_prior_insights) and persistence
(save_insight), plus the read queries backing GET /insights/history (the
dashboard's insight tracker). Status transitions (pending -> strengthening ->
confirmed/expired) and outcome_pct resolution are agent/resolve_insights.py's
job (spec 11) — insights are created here as 'pending' and updated later by
that module via update_insight_status().
"""
import json
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_insight(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    symbol: str,
    trade_date: str,
    signal_type: str,
    action: str,
    confidence: str,
    narrative: str,
    evidence: dict,
    price_at_insight: float,
) -> int:
    # Upsert on (user_id, symbol, trade_date) — see schema.sql's
    # idx_insights_unique_per_day. Re-running the digest for a day that
    # already has an insight for this symbol replaces it (fresh
    # signal/narrative/evidence, status reset to 'pending') rather than
    # inserting a sibling row, which is what caused Needs Attention to show
    # duplicate cards after repeated same-day runs.
    conn.execute(
        """
        INSERT INTO insights (
            user_id, symbol, trade_date, signal_type, action, confidence, narrative,
            evidence_json, status, price_at_insight, outcome_pct, created_at, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, ?, NULL)
        ON CONFLICT (user_id, symbol, trade_date) DO UPDATE SET
            signal_type = excluded.signal_type,
            action = excluded.action,
            confidence = excluded.confidence,
            narrative = excluded.narrative,
            evidence_json = excluded.evidence_json,
            status = 'pending',
            price_at_insight = excluded.price_at_insight,
            outcome_pct = NULL,
            created_at = excluded.created_at,
            resolved_at = NULL
        """,
        (
            user_id,
            symbol,
            trade_date,
            signal_type,
            action,
            confidence,
            narrative,
            json.dumps(evidence),
            price_at_insight,
            _now_iso(),
        ),
    )
    conn.commit()
    # lastrowid/rowcount semantics for an ON CONFLICT DO UPDATE vary by SQLite
    # build, so fetch the row explicitly rather than trust either.
    row = conn.execute(
        "SELECT id FROM insights WHERE user_id = ? AND symbol = ? AND trade_date = ?",
        (user_id, symbol, trade_date),
    ).fetchone()
    return row["id"]


def get_prior_insights(conn: sqlite3.Connection, user_id: str, symbol: str, limit: int = 5) -> list[dict]:
    """Most recent past insights for this user/symbol, newest first — what the
    digest graph's compare_prior node reconciles the day's new read against."""
    rows = conn.execute(
        """
        SELECT * FROM insights
        WHERE user_id = ? AND symbol = ?
        ORDER BY trade_date DESC, id DESC
        LIMIT ?
        """,
        (user_id, symbol, limit),
    ).fetchall()
    return [_deserialize(r) for r in rows]


def get_insights_history(conn: sqlite3.Connection, user_id: str, symbol: str | None = None) -> list[dict]:
    if symbol is None:
        rows = conn.execute(
            "SELECT * FROM insights WHERE user_id = ? ORDER BY trade_date DESC, id DESC",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM insights WHERE user_id = ? AND symbol = ? ORDER BY trade_date DESC, id DESC",
            (user_id, symbol),
        ).fetchall()
    return [_deserialize(r) for r in rows]


def get_unresolved_insights(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """pending/strengthening insights — candidates for resolve_insights.py to
    re-check against fresh price data."""
    rows = conn.execute(
        "SELECT * FROM insights WHERE user_id = ? AND status IN ('pending', 'strengthening')",
        (user_id,),
    ).fetchall()
    return [_deserialize(r) for r in rows]


def update_insight_status(
    conn: sqlite3.Connection,
    insight_id: int,
    *,
    status: str,
    outcome_pct: float,
    resolved: bool,
) -> None:
    """outcome_pct is updated on every check (even when status doesn't
    change) so the tracker shows live movement, not just a final number;
    resolved_at is only set when `resolved` is True (the confirmed/expired
    terminal transition) — see specs/11_insight_tracker_resolution.md."""
    conn.execute(
        "UPDATE insights SET status = ?, outcome_pct = ?, resolved_at = ? WHERE id = ?",
        (status, outcome_pct, _now_iso() if resolved else None, insight_id),
    )
    conn.commit()


def _deserialize(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["evidence"] = json.loads(d.pop("evidence_json"))
    return d
