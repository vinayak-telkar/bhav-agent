"""
Tests for agent/resolve_insights.py — deterministic status resolution, no
LLM involved. Uses real seeded price data but sets price_at_insight
explicitly per test so each scenario (confirmed/expired/strengthening/
untouched) is fully controlled rather than depending on the seed RNG's
exact trajectory.
"""
import pytest

from agent import resolve_insights
from app.data import db as db_module
from app.data import insights as insights_dal


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "resolve_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


def _trade_dates_and_closes(db_path, symbol):
    conn = db_module.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, close FROM daily_bars WHERE symbol = ? ORDER BY trade_date", (symbol,)
        ).fetchall()
        return [(r["trade_date"], r["close"]) for r in rows]
    finally:
        conn.close()


def _insert_insight(db_path, *, symbol, trade_date, signal_type, price_at_insight):
    conn = db_module.get_connection(db_path)
    try:
        insight_id = insights_dal.save_insight(
            conn,
            user_id="demo-user-0001",
            symbol=symbol,
            trade_date=trade_date,
            signal_type=signal_type,
            action="Hold, no action",
            confidence="high",
            narrative="test insight",
            evidence={},
            price_at_insight=price_at_insight,
        )
        return insight_id
    finally:
        conn.close()


def _get_insight(db_path, insight_id):
    conn = db_module.get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def test_confirms_up_signal_after_horizon(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    early_date, _ = dates[0]
    latest_close = dates[-1][1]
    price_at_insight = latest_close / 1.10  # guarantees ~+10% outcome, well past the 3% threshold

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=early_date,
        signal_type="genuine_accumulation", price_at_insight=price_at_insight,
    )

    updated = resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    assert any(u["symbol"] == "DEMOACCUM" and u["status"] == "confirmed" for u in updated)
    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "confirmed"
    assert row["resolved_at"] is not None
    assert row["outcome_pct"] > 3.0


def test_expires_when_price_moved_wrong_direction(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    early_date, _ = dates[0]
    latest_close = dates[-1][1]
    price_at_insight = latest_close / 1.10  # price actually rose ~10%

    # leveraged_rally expects DOWN — price rising instead means the thesis didn't hold
    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=early_date,
        signal_type="leveraged_rally", price_at_insight=price_at_insight,
    )

    resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "expired"
    assert row["resolved_at"] is not None


def test_strengthening_before_horizon(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    # Only 2 sessions remain after this date — under HORIZON_SESSIONS (5)
    near_end_date, _ = dates[-3]
    latest_close = dates[-1][1]
    price_at_insight = latest_close / 1.05  # clear early +5% move, above the 1.5% strengthen threshold

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=near_end_date,
        signal_type="genuine_accumulation", price_at_insight=price_at_insight,
    )

    resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "strengthening"
    assert row["resolved_at"] is None  # not a terminal state


def test_untouched_when_no_new_sessions_have_passed(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    latest_date, latest_close = dates[-1]

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=latest_date,
        signal_type="genuine_accumulation", price_at_insight=latest_close,
    )

    updated = resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    assert updated == []
    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "pending"
    assert row["outcome_pct"] is None


def test_no_signal_expires_without_ever_strengthening(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    early_date, _ = dates[0]

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=early_date,
        signal_type="no_signal", price_at_insight=dates[0][1],
    )

    resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "expired"


def test_already_resolved_insights_are_never_touched(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    early_date, _ = dates[0]

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=early_date,
        signal_type="genuine_accumulation", price_at_insight=dates[0][1],
    )
    conn = db_module.get_connection(seeded_db_path)
    conn.execute("UPDATE insights SET status = 'confirmed', outcome_pct = 42.0 WHERE id = ?", (insight_id,))
    conn.commit()
    conn.close()

    updated = resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    assert updated == []
    row = _get_insight(seeded_db_path, insight_id)
    assert row["outcome_pct"] == 42.0  # untouched, not recomputed


def test_outcome_pct_updates_even_when_status_unchanged(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    # Few sessions elapsed (under HORIZON_SESSIONS) AND a small move (under
    # STRENGTHEN_THRESHOLD_PCT) -> status genuinely stays 'pending', but
    # outcome_pct should still be recorded (live progress, not just final).
    near_end_date, _ = dates[-3]
    latest_close = dates[-1][1]
    price_at_insight = latest_close / 1.005

    insight_id = _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=near_end_date,
        signal_type="genuine_accumulation", price_at_insight=price_at_insight,
    )

    resolve_insights.resolve_insights("demo-user-0001", db_path=seeded_db_path)

    row = _get_insight(seeded_db_path, insight_id)
    assert row["status"] == "pending"
    assert row["outcome_pct"] is not None
    assert row["outcome_pct"] < resolve_insights.STRENGTHEN_THRESHOLD_PCT


def test_stream_yields_readable_progress(seeded_db_path):
    dates = _trade_dates_and_closes(seeded_db_path, "DEMOACCUM")
    early_date, _ = dates[0]
    latest_close = dates[-1][1]
    _insert_insight(
        seeded_db_path, symbol="DEMOACCUM", trade_date=early_date,
        signal_type="genuine_accumulation", price_at_insight=latest_close / 1.10,
    )

    messages = list(resolve_insights.stream_resolve_insights("demo-user-0001", db_path=seeded_db_path))

    assert any("Re-checking 1 prior insight" in m for m in messages)
    assert any("DEMOACCUM" in m and "confirmed" in m for m in messages)
