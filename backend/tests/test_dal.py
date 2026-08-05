"""DAL behavior tests against the seeded demo dataset."""
import pytest

from app.data import bars, fo, insights, symbols, watchlist


def test_get_stock_snapshot_excludes_corporate_action_rows_from_baseline(seeded_db):
    snapshot = bars.get_stock_snapshot(seeded_db, "DEMOBE")
    assert snapshot is not None
    assert snapshot["deliv_pct_20d_avg"] is not None  # baseline computed without error


def test_get_stock_snapshot_unknown_symbol_returns_none(seeded_db):
    assert bars.get_stock_snapshot(seeded_db, "NOSUCHSYMBOL") is None


def test_reliance_shows_accumulation_pattern(seeded_db):
    snapshot = bars.get_stock_snapshot(seeded_db, "DEMOACCUM")
    assert snapshot["deliv_pct"] > snapshot["deliv_pct_20d_avg"]
    assert snapshot["close"] > snapshot["prev_close"] or snapshot["close"] > 3000


def test_tcs_delivery_trend_is_falling(seeded_db):
    trend = bars.get_delivery_trend(seeded_db, "DEMORALLY", days=25)
    assert trend["deliv_qty_trend"] == "falling"


def test_tcs_fo_positioning_shows_long_buildup(seeded_db):
    positioning = fo.get_fo_positioning(seeded_db, "DEMORALLY")
    assert positioning["buildup_classification"] == "long_buildup"
    assert positioning["basis"] > 0


def test_fo_positioning_none_for_non_fo_symbol(seeded_db):
    assert fo.get_fo_positioning(seeded_db, "DEMOSMALL") is None


def test_watchlist_rejects_held_without_size_bucket(empty_db):
    empty_db.execute("INSERT INTO users (user_id, created_at) VALUES ('u1', '2026-01-01')")
    empty_db.execute(
        "INSERT INTO symbols (symbol, name, series, last_updated) VALUES ('ABC', 'ABC Ltd', 'EQ', '2026-01-01')"
    )
    empty_db.commit()
    with pytest.raises(watchlist.ValidationError):
        watchlist.add_watch(empty_db, "u1", "ABC", "held", size_bucket=None)


def test_watchlist_promotion_wishlist_to_held(seeded_db):
    promoted = watchlist.promote_to_held(seeded_db, "demo-user-0001", "DEMODIST", "medium")
    assert promoted["status"] == "held"
    assert promoted["size_bucket"] == "medium"
    remaining_wishlist = {w["symbol"] for w in watchlist.get_wishlist(seeded_db, "demo-user-0001")}
    assert "DEMODIST" not in remaining_wishlist


def test_symbol_search_prefix_ranks_first(seeded_db):
    results = symbols.search_symbols(seeded_db, "DEMORALLY")
    assert results[0]["symbol"] == "DEMORALLY"


def test_save_and_get_prior_insights(seeded_db):
    insight_id = insights.save_insight(
        seeded_db,
        user_id="demo-user-0001",
        symbol="DEMOACCUM",
        trade_date="2026-07-31",
        signal_type="accumulation",
        action="hold",
        confidence="high",
        narrative="Delivery is 65.19% vs a 52.57% baseline.",
        evidence={"deliv_pct_today": 65.19, "deliv_pct_20d_avg": 52.57},
        price_at_insight=3191.59,
    )
    assert insight_id > 0
    prior = insights.get_prior_insights(seeded_db, "demo-user-0001", "DEMOACCUM")
    assert prior[0]["evidence"]["deliv_pct_today"] == 65.19
    assert prior[0]["status"] == "pending"
    assert prior[0]["narrative"] == "Delivery is 65.19% vs a 52.57% baseline."


def test_save_insight_same_day_replaces_rather_than_duplicates(seeded_db):
    """Confirmed live (2026-08-05): re-running the digest for a day that
    already had an insight for a symbol (manual re-trigger, or repeated
    testing runs) created a sibling row instead of replacing it — Needs
    Attention/Insight Tracker then showed duplicate cards for the same
    symbol+day. save_insight must upsert on (user_id, symbol, trade_date)."""
    first_id = insights.save_insight(
        seeded_db,
        user_id="demo-user-0001",
        symbol="DEMOACCUM",
        trade_date="2026-07-31",
        signal_type="genuine_accumulation",
        action="Hold, no action",
        confidence="high",
        narrative="First read of the day.",
        evidence={"deliv_pct_today": 65.19},
        price_at_insight=3191.59,
    )
    second_id = insights.save_insight(
        seeded_db,
        user_id="demo-user-0001",
        symbol="DEMOACCUM",
        trade_date="2026-07-31",
        signal_type="genuine_accumulation",
        action="Hold, no action",
        confidence="medium",
        narrative="Second read, same day.",
        evidence={"deliv_pct_today": 66.0},
        price_at_insight=3195.0,
    )
    assert first_id == second_id
    history = insights.get_insights_history(seeded_db, "demo-user-0001", "DEMOACCUM")
    same_day = [h for h in history if h["trade_date"] == "2026-07-31"]
    assert len(same_day) == 1
    assert same_day[0]["narrative"] == "Second read, same day."
    assert same_day[0]["confidence"] == "medium"
