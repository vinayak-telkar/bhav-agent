"""
Ingest parser tests against fixtures shaped like NSE's real file formats —
confirmed against actual downloaded files (2026-08-03, via the manual-ingest
flow; see specs/01's Changelog). The cash/F&O UDiFF files use a clean ","
delimiter (matches the fixture below); the delivery file uses ", " (comma +
space) as its actual separator — DELIVERY_CSV replicates that exactly,
since a plain-comma fixture would silently hide the skipinitialspace bug
this format requires (found and fixed against the real file).
"""
from datetime import date

from ingest import bhavcopy

CASH_CSV = """TradDt,Sgmt,FinInstrmTp,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd
2026-07-31,CM,,RELIANCE,EQ,3200.00,3230.00,3190.00,3220.00,3195.00,6000000,1.9e10,150000
2026-07-31,CM,,BEALERT,EQ,300.00,305.00,295.00,150.00,300.00,500000,7.5e7,4000
"""

# NSE's real sec_bhavdata_full format: ", " (comma + space) separators, not a bare ",".
DELIVERY_CSV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
RELIANCE, EQ, 31-Jul-2026, 3195.00, 3200.00, 3230.00, 3190.00, 3220.00, 3220.00, 3212.50, 6000000, 190000, 150000, 3900000, 65.00
BEALERT, EQ, 31-Jul-2026, 300.00, 300.00, 305.00, 295.00, 150.00, 150.00, 220.00, 500000, 7500, 4000, 260000, 52.00
"""

FO_CSV = """TradDt,FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,ClsPric,OpnIntrst,ChngInOpnIntrst
2026-07-31,STF,RELIANCE,2026-08-27,,,3225.00,20000000,800000
2026-07-31,STO,RELIANCE,2026-08-27,3300,CE,15.0,500000,
2026-07-31,STO,RELIANCE,2026-08-27,3100,PE,12.0,700000,
"""


def test_parse_cash_udiff_extracts_cm_segment_rows():
    rows = bhavcopy._parse_cash_udiff(CASH_CSV, date(2026, 7, 31))
    assert set(rows.keys()) == {"RELIANCE", "BEALERT"}
    assert rows["RELIANCE"]["close"] == 3220.00
    assert rows["RELIANCE"]["volume"] == 6000000
    assert rows["BEALERT"]["prev_close"] == 300.00


def test_parse_delivery_file_extracts_deliv_pct():
    delivery = bhavcopy._parse_delivery_file(DELIVERY_CSV)
    assert delivery["RELIANCE"]["deliv_pct"] == 65.00
    assert delivery["RELIANCE"]["deliv_qty"] == 3900000


def test_parse_fo_udiff_rolls_up_per_underlying():
    fo_rows = bhavcopy._parse_fo_udiff(FO_CSV, date(2026, 7, 31))
    assert "RELIANCE" in fo_rows
    row = fo_rows["RELIANCE"]
    assert row["fut_close"] == 3225.00
    assert row["fut_oi"] == 20000000
    assert row["max_call_oi_strike"] == 3300.0
    assert row["max_put_oi_strike"] == 3100.0
    assert row["pcr"] == round(700000 / 500000, 4)


def test_detect_corporate_actions_flags_large_overnight_jump():
    rows = bhavcopy._parse_cash_udiff(CASH_CSV, date(2026, 7, 31))
    flagged = bhavcopy._detect_corporate_actions(rows)
    assert flagged == ["BEALERT"]  # 300 -> 150 is a 50% jump; RELIANCE's move is small


def test_ingest_date_idempotent_upsert(seeded_db, monkeypatch, tmp_path):
    """Simulates a full ingest_date() run via monkeypatched network calls,
    then re-runs it to confirm upsert (not duplicate-insert) semantics."""
    db_path = str(tmp_path / "ingest_test.db")
    from app.data import db as db_module

    db_module.init_db(db_path)

    monkeypatch.setattr(bhavcopy, "_warm_up_session", lambda: _FakeClient())
    monkeypatch.setattr(bhavcopy, "RAW_DIR", tmp_path / "raw")

    call_count = {"n": 0}

    def fake_download(client, url, max_retries=3):
        call_count["n"] += 1
        if "sec_bhavdata_full" in url:
            return DELIVERY_CSV.encode()
        if "FO" in url:
            return FO_CSV.encode()
        return CASH_CSV.encode()

    monkeypatch.setattr(bhavcopy, "_download_with_retry", fake_download)

    result1 = bhavcopy.ingest_date(date(2026, 7, 31), db_path)
    assert result1.status == "ok"
    assert result1.symbols_loaded == 2
    assert result1.corporate_actions_flagged == ["BEALERT"]

    conn = db_module.get_connection(db_path)
    count_after_first = conn.execute("SELECT COUNT(*) AS n FROM daily_bars").fetchone()["n"]
    conn.close()

    result2 = bhavcopy.ingest_date(date(2026, 7, 31), db_path)
    assert result2.status == "skipped_already_ingested"

    conn = db_module.get_connection(db_path)
    count_after_second = conn.execute("SELECT COUNT(*) AS n FROM daily_bars").fetchone()["n"]
    reliance_method = conn.execute(
        "SELECT closing_price_method FROM daily_bars WHERE symbol = 'RELIANCE'"
    ).fetchone()["closing_price_method"]
    conn.close()

    assert count_after_first == count_after_second == 2
    assert reliance_method == "cas_auction"  # RELIANCE has an FO row -> Category I


class _FakeClient:
    def close(self):
        pass
