"""
Tests for the manual-download ingest path (ingest/local_ingest.py) — see its
module docstring for why this exists (NSE's Akamai bot-detection blocks
scripted downloads even from a normal home network, confirmed in this
project's build session). Filename matching is tested against real files on
disk (tmp_path), not mocks, since that's exactly what's fragile here.
"""
from datetime import date, datetime, timedelta

from ingest import local_ingest

CASH_CSV = """TradDt,Sgmt,FinInstrmTp,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd
2026-08-02,CM,,TESTSYM,EQ,100.00,105.00,98.00,103.00,99.00,50000,5000000,500
"""

DELIVERY_CSV = """SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,NO_OF_TRADES,DELIV_QTY,DELIV_PER
TESTSYM,EQ,02-AUG-2026,99.00,100.00,105.00,98.00,103.00,103.00,101.50,50000,500,500,32000,64.00
"""


def test_find_local_files_matches_exact_expected_name(tmp_path):
    trade_date = date(2026, 8, 2)
    (tmp_path / "BhavCopy_NSE_CM_0_0_0_20260802_F_0000.csv").write_text(CASH_CSV)

    matches = local_ingest.find_local_files(tmp_path, trade_date)
    assert matches["cash"].matched_path == tmp_path / "BhavCopy_NSE_CM_0_0_0_20260802_F_0000.csv"
    assert matches["delivery"].matched_path is None
    assert matches["fo"].matched_path is None


def test_find_local_files_tolerates_browser_duplicate_suffix(tmp_path):
    trade_date = date(2026, 8, 2)
    (tmp_path / "BhavCopy_NSE_CM_0_0_0_20260802_F_0000 (1).csv").write_text(CASH_CSV)

    matches = local_ingest.find_local_files(tmp_path, trade_date)
    assert matches["cash"].matched_path is not None
    assert matches["cash"].matched_path.name == "BhavCopy_NSE_CM_0_0_0_20260802_F_0000 (1).csv"


def test_find_local_files_case_insensitive(tmp_path):
    trade_date = date(2026, 8, 2)
    (tmp_path / "bhavcopy_nse_cm_0_0_0_20260802_f_0000.csv").write_text(CASH_CSV)

    matches = local_ingest.find_local_files(tmp_path, trade_date)
    assert matches["cash"].matched_path is not None


def test_find_local_files_surfaces_unmatched_recent_files_as_candidates(tmp_path):
    trade_date = date(2026, 8, 2)
    (tmp_path / "some_random_download.csv").write_text("not a bhavcopy file")

    matches = local_ingest.find_local_files(tmp_path, trade_date)
    assert matches["cash"].matched_path is None
    assert tmp_path / "some_random_download.csv" in matches["cash"].candidates
    # same candidate list offered for every file type — the user picks which is which
    assert matches["delivery"].candidates == matches["cash"].candidates


def test_find_local_files_excludes_old_files_from_candidates(tmp_path):
    import os

    trade_date = date(2026, 8, 2)
    old_file = tmp_path / "ancient_download.csv"
    old_file.write_text("old")
    old_timestamp = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    matches = local_ingest.find_local_files(tmp_path, trade_date)
    assert old_file not in matches["cash"].candidates


def test_ingest_from_local_files_loads_cash_only(tmp_path):
    from app.data import db as db_module

    db_path = str(tmp_path / "local_ingest_test.db")
    db_module.init_db(db_path)

    cash_file = tmp_path / "cash.csv"
    cash_file.write_text(CASH_CSV)

    result = local_ingest.ingest_from_local_files(date(2026, 8, 2), cash_file, db_path=db_path)
    assert result.status == "ok"
    assert result.symbols_loaded == 1

    conn = db_module.get_connection(db_path)
    row = conn.execute("SELECT * FROM daily_bars WHERE symbol = 'TESTSYM'").fetchone()
    conn.close()
    assert row["close"] == 103.0
    assert row["deliv_pct"] is None  # no delivery file provided
    assert row["closing_price_method"] == "vwap_30min"  # no F&O file -> not Category I


def test_ingest_from_local_files_loads_cash_and_delivery(tmp_path):
    from app.data import db as db_module

    db_path = str(tmp_path / "local_ingest_test2.db")
    db_module.init_db(db_path)

    cash_file = tmp_path / "cash.csv"
    cash_file.write_text(CASH_CSV)
    delivery_file = tmp_path / "delivery.csv"
    delivery_file.write_text(DELIVERY_CSV)

    result = local_ingest.ingest_from_local_files(
        date(2026, 8, 2), cash_file, delivery_file, db_path=db_path
    )
    assert result.status == "ok"

    conn = db_module.get_connection(db_path)
    row = conn.execute("SELECT * FROM daily_bars WHERE symbol = 'TESTSYM'").fetchone()
    conn.close()
    assert row["deliv_pct"] == 64.0


def test_ingest_from_local_files_missing_cash_file_fails_gracefully(tmp_path):
    from app.data import db as db_module

    db_path = str(tmp_path / "local_ingest_test3.db")
    db_module.init_db(db_path)

    result = local_ingest.ingest_from_local_files(
        date(2026, 8, 2), tmp_path / "does_not_exist.csv", db_path=db_path
    )
    assert result.status == "failed"
    assert "not found" in result.error


def test_nse_download_links_has_all_three_file_types():
    links = local_ingest.nse_download_links(date(2026, 8, 2))
    file_types = {link["file_type"] for link in links}
    assert file_types == {"cash", "delivery", "fo"}
    for link in links:
        assert "20260802" in link["direct_url"] or "02082026" in link["direct_url"]
