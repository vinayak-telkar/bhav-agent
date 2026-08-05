"""FastAPI route tests for POST /chat (app/routes/chat.py). Monkeypatches
agent.chat_graph.answer/stream_answer to avoid needing a live Groq key —
agent/chat_graph.py's own logic is covered by tests/test_chat_graph.py."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "chat_routes_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    from app.data import db as db_module

    db_module.init_db(db_path)
    db_module.ensure_demo_user("demo-user-0001", db_path)

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_chat_route_returns_answer(client, monkeypatch):
    async def fake_answer(user_id, question, db_path=None):
        assert user_id == "demo-user-0001"
        assert question == "why DEMOACCUM?"
        return "Because delivery is high relative to baseline."

    import app.routes.chat as chat_route

    monkeypatch.setattr(chat_route, "answer", fake_answer)

    resp = client.post("/chat", json={"question": "why DEMOACCUM?"})
    assert resp.status_code == 200
    assert resp.json() == {"answer": "Because delivery is high relative to baseline."}


def test_chat_stream_route_yields_progress_then_answer(client, monkeypatch):
    async def fake_stream_answer(user_id, question, db_path=None):
        yield ("progress", "Looking into that…")
        yield ("answer", "Here you go.")

    import app.routes.chat as chat_route

    monkeypatch.setattr(chat_route, "stream_answer", fake_stream_answer)

    with client.stream("GET", "/chat/stream", params={"question": "hi"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "Looking into that" in body
    assert "Here you go." in body
