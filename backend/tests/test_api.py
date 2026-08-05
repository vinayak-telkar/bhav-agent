"""
FastAPI route tests against the seeded DB. POST /digest/run is intentionally
NOT exercised here — it drives the real digest graph against live
ChatGroq/ChatCerebras, which needs an API key not available in this build
environment (see specs/02's Changelog). Verify that route manually once a
GROQ_API_KEY is configured — see TESTING.md.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_db_path(tmp_path):
    from ingest import seed_data

    db_path = str(tmp_path / "api_test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    return db_path


@pytest.fixture
def client(seeded_db_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", seeded_db_path)
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_search_symbols(client):
    resp = client.get("/symbols", params={"q": "ACCUM"})
    assert resp.status_code == 200
    symbols = {s["symbol"] for s in resp.json()}
    assert "DEMOACCUM" in symbols


def test_search_symbols_empty_query_returns_empty_list(client):
    resp = client.get("/symbols")
    assert resp.json() == []


def test_get_watchlist_all(client):
    resp = client.get("/watchlist")
    assert resp.status_code == 200
    assert len(resp.json()) == 5


def test_get_watchlist_filtered_by_status(client):
    resp = client.get("/watchlist", params={"status": "held"})
    symbols = {r["symbol"] for r in resp.json()}
    assert symbols == {"DEMOACCUM", "DEMORALLY", "DEMOBE"}


def test_promote_wishlist_to_held(client):
    resp = client.patch("/watch/DEMODIST", json={"status": "held", "size_bucket": "medium"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "held"

    held = client.get("/watchlist", params={"status": "held"}).json()
    assert "DEMODIST" in {r["symbol"] for r in held}


def test_add_watch_rejects_held_without_size_bucket(client):
    resp = client.post("/watch", json={"symbol": "DEMOACCUM", "status": "held"})
    assert resp.status_code == 400


def test_remove_watch(client):
    resp = client.delete("/watch/DEMOSMALL")
    assert resp.status_code == 204
    remaining = {r["symbol"] for r in client.get("/watchlist").json()}
    assert "DEMOSMALL" not in remaining


def test_holdings_includes_snapshot(client):
    resp = client.get("/holdings")
    assert resp.status_code == 200
    holdings = resp.json()
    reliance = next(h for h in holdings if h["symbol"] == "DEMOACCUM")
    assert reliance["snapshot"]["deliv_pct"] > reliance["snapshot"]["deliv_pct_20d_avg"]


def test_digest_today_empty_before_any_run(client):
    resp = client.get("/digest/today")
    assert resp.status_code == 200
    assert resp.json()["insights"] == []


def test_insights_history_empty_before_any_run(client):
    resp = client.get("/insights/history")
    assert resp.status_code == 200
    assert resp.json() == []
