"""GET /symbols?q= — autocomplete from the symbols reference table."""
from fastapi import APIRouter, Depends
from sqlite3 import Connection

from app.data import symbols as symbols_dal
from app.deps import get_db

router = APIRouter()


@router.get("/symbols")
def search_symbols(q: str = "", conn: Connection = Depends(get_db)) -> list[dict]:
    if not q:
        return []
    return symbols_dal.search_symbols(conn, q)
