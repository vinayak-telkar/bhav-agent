"""
Connection factory + PRAGMA setup. Every DAL module and the MCP server go
through get_connection() so there is exactly one place that knows the DB
path and pragma configuration.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _default_db_path() -> str:
    return os.environ.get("DATABASE_PATH", "../data/bhav.db")


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Opens a SQLite connection with WAL mode + foreign keys enabled.

    check_same_thread=False: FastAPI dispatches sync dependencies (app/deps.py's
    get_db, a generator) and sync route handlers to threadpool workers as
    separate calls with no guaranteed thread affinity between them — so a
    connection opened in get_db()'s setup can legitimately get used from a
    different thread than the one that created it, which sqlite3 blocks by
    default ("SQLite objects created in a thread can only be used in that
    same thread"). Safe to disable here because every connection this
    factory returns is single-owner and used sequentially within one
    request/call (open -> use -> close) — never concurrently from multiple
    threads at once, which is the actual case check_same_thread guards
    against.
    """
    conn = sqlite3.connect(db_path or _default_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Applies schema.sql to the target DB. Idempotent — every statement is
    CREATE TABLE/INDEX IF NOT EXISTS."""
    conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def ensure_demo_user(user_id: str, db_path: str | None = None) -> None:
    """Bootstraps the single hardcoded demo user row (tech spec §7) so
    watchlist/insights FK constraints are satisfiable on a fresh DB, without
    depending on ingest/seed_data.py — real usage starts with an empty
    watchlist and no synthetic data (see ingest/seed_data.py's docstring for
    why synthetic data no longer lives in the app's real database). Idempotent."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
