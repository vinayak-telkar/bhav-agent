"""Schema-level acceptance criteria — specs/03_db_schema.md."""
import sqlite3

import pytest


def test_foreign_keys_enforced(empty_db):
    with pytest.raises(sqlite3.IntegrityError):
        empty_db.execute(
            "INSERT INTO watchlist (user_id, symbol, status, size_bucket, added_at) "
            "VALUES ('no-such-user', 'NOSYM', 'held', 'small', '2026-01-01')"
        )
        empty_db.commit()


def test_insights_round_trip(empty_db):
    empty_db.execute("INSERT INTO users (user_id, created_at) VALUES ('u1', '2026-01-01')")
    empty_db.execute(
        "INSERT INTO symbols (symbol, name, series, last_updated) VALUES ('ABC', 'ABC Ltd', 'EQ', '2026-01-01')"
    )
    empty_db.commit()
    empty_db.execute(
        """
        INSERT INTO insights (
            user_id, symbol, trade_date, signal_type, action, confidence, narrative,
            evidence_json, status, price_at_insight, created_at
        ) VALUES ('u1', 'ABC', '2026-01-02', 'accumulation', 'hold', 'high',
                   'Delivery 60% vs baseline.', '{"deliv_pct": 60}', 'pending', 100.0,
                   '2026-01-02T00:00:00Z')
        """
    )
    empty_db.commit()
    row = empty_db.execute("SELECT * FROM insights WHERE user_id = 'u1'").fetchone()
    assert row["signal_type"] == "accumulation"
    assert row["evidence_json"] == '{"deliv_pct": 60}'


def test_seed_includes_category_one_and_two_same_dates(seeded_db):
    rows = seeded_db.execute(
        """
        SELECT symbol, closing_price_method FROM daily_bars
        WHERE trade_date = (SELECT MAX(trade_date) FROM daily_bars)
        """
    ).fetchall()
    methods = {r["symbol"]: r["closing_price_method"] for r in rows}
    assert methods["DEMOACCUM"] == "cas_auction"
    assert methods["DEMOSMALL"] == "vwap_30min"


def test_seed_includes_corporate_action_flag(seeded_db):
    row = seeded_db.execute(
        "SELECT COUNT(*) AS n FROM daily_bars WHERE corporate_action_flag = 1"
    ).fetchone()
    assert row["n"] >= 1


def test_seed_includes_series_change(seeded_db):
    rows = seeded_db.execute(
        "SELECT DISTINCT series FROM daily_bars WHERE symbol = 'DEMOBE'"
    ).fetchall()
    series_values = {r["series"] for r in rows}
    assert series_values == {"EQ", "BE"}
