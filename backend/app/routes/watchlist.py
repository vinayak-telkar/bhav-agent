"""
GET /watchlist, POST /watch, PATCH /watch/{symbol}, DELETE /watch/{symbol}.
"I bought it" promotion (PRD §5) is just a PATCH with status='held' and a
size_bucket — no separate route needed, update_watch already handles it.
"""
from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import DEMO_USER_ID
from app.data import bars as bars_dal
from app.data import watchlist as watchlist_dal
from app.deps import get_db

router = APIRouter()

SizeBucket = Literal["small", "medium", "large"]
Status = Literal["held", "wishlist"]


class AddWatchRequest(BaseModel):
    symbol: str
    status: Status
    size_bucket: SizeBucket | None = None


class UpdateWatchRequest(BaseModel):
    status: Status | None = None
    size_bucket: SizeBucket | None = None


@router.get("/watchlist")
def get_watchlist(status: Status | None = None, conn: Connection = Depends(get_db)) -> list[dict]:
    # Snapshot attached the same way /holdings does (app/routes/digest.py) —
    # wishlist symbols show up in Needs Attention too, and its glance-able
    # visuals (delivery gauge, price-change badge) need the same structured
    # close/prev_close/deliv_pct data holdings already carries.
    items = watchlist_dal.get_watchlist(conn, DEMO_USER_ID, status)
    return [{**item, "snapshot": bars_dal.get_stock_snapshot(conn, item["symbol"])} for item in items]


@router.post("/watch", status_code=201)
def add_watch(body: AddWatchRequest, conn: Connection = Depends(get_db)) -> dict:
    try:
        return watchlist_dal.add_watch(conn, DEMO_USER_ID, body.symbol, body.status, body.size_bucket)
    except watchlist_dal.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/watch/{symbol}")
def update_watch(symbol: str, body: UpdateWatchRequest, conn: Connection = Depends(get_db)) -> dict:
    try:
        return watchlist_dal.update_watch(conn, DEMO_USER_ID, symbol, status=body.status, size_bucket=body.size_bucket)
    except watchlist_dal.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/watch/{symbol}", status_code=204)
def remove_watch(symbol: str, conn: Connection = Depends(get_db)) -> None:
    watchlist_dal.remove_watch(conn, DEMO_USER_ID, symbol)
