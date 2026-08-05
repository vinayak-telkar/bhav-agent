import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.data import db
from ingest import seed_data


@pytest.fixture
def seeded_db(tmp_path):
    """A freshly seeded SQLite DB, isolated per test via tmp_path."""
    db_path = str(tmp_path / "test.db")
    seed_data.seed(db_path, end_date=seed_data.date(2026, 7, 31))
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def empty_db(tmp_path):
    """A schema-only DB, no seed rows — for constraint/validation tests."""
    db_path = str(tmp_path / "empty.db")
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()
