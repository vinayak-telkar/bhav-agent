"""FastAPI route tests for the manual-download ingest flow (app/routes/ingest.py)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "ingest_routes_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


@pytest.fixture
def client(seeded_db_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", seeded_db_path)
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_get_links_returns_three_file_types(client):
    resp = client.get("/ingest/links", params={"trade_date": "2026-08-02"})
    assert resp.status_code == 200
    file_types = {link["file_type"] for link in resp.json()}
    assert file_types == {"cash", "delivery", "fo"}


def test_check_downloads_reports_not_found_for_empty_dir(client, tmp_path):
    resp = client.post(
        "/ingest/check",
        json={"trade_date": "2026-08-02", "downloads_dir": str(tmp_path)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash"]["matched_path"] is None
    assert body["cash"]["candidates"] == []


def test_check_downloads_finds_matching_file(client, tmp_path):
    (tmp_path / "BhavCopy_NSE_CM_0_0_0_20260802_F_0000.csv").write_text("dummy")
    resp = client.post(
        "/ingest/check",
        json={"trade_date": "2026-08-02", "downloads_dir": str(tmp_path)},
    )
    body = resp.json()
    assert body["cash"]["matched_path"] is not None
    assert "BhavCopy_NSE_CM_0_0_0_20260802_F_0000.csv" in body["cash"]["matched_path"]


def test_run_local_ingest_success(client, tmp_path):
    cash_csv = (
        "TradDt,Sgmt,FinInstrmTp,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd\n"
        "2026-08-02,CM,,NEWSYM,EQ,50.00,52.00,49.00,51.00,50.00,10000,510000,100\n"
    )
    cash_file = tmp_path / "cash.csv"
    cash_file.write_text(cash_csv)

    resp = client.post(
        "/ingest/run-local",
        json={"trade_date": "2026-08-02", "cash_file": str(cash_file)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["symbols_loaded"] == 1


def test_run_local_ingest_missing_file_reports_failure(client):
    resp = client.post(
        "/ingest/run-local",
        json={"trade_date": "2026-08-02", "cash_file": "/no/such/file.csv"},
    )
    assert resp.status_code == 200  # ingest reports failure in-band, not an HTTP error
    body = resp.json()
    assert body["status"] == "failed"
    assert "not found" in body["error"]
