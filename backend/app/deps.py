"""FastAPI dependency: one short-lived SQLite connection per request."""
from collections.abc import Iterator
from sqlite3 import Connection

from app.data import db as db_module


def get_db() -> Iterator[Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()
