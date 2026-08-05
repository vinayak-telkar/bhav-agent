"""
GET /digest/today, GET /holdings, GET /insights/history — read-only dashboard
feeds from persisted agent output (no LLM call on page load, tech spec §7).

POST /digest/run and GET /digest/run-stream are demo/testing conveniences
not in the original route list: manually trigger a digest run instead of
waiting for the scheduled cron job, so the dashboard can be exercised end to
end without sitting through a real wait. See TESTING.md.

Both first resolve prior insights' status (spec 11 — "check what happened to
past calls" before "make new calls," the order a real analyst would work
in), then generate new ones.
"""
import json
from sqlite3 import Connection

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agent.digest_graph import run_daily_digest, stream_daily_digest
from agent.resolve_insights import resolve_insights, stream_resolve_insights
from app.config import DEMO_USER_ID
from app.data import bars as bars_dal
from app.data import insights as insights_dal
from app.data import watchlist as watchlist_dal
from app.deps import get_db

router = APIRouter()


@router.get("/digest/today")
def digest_today(conn: Connection = Depends(get_db)) -> dict:
    latest_date = bars_dal.get_latest_trade_date(conn)
    all_insights = insights_dal.get_insights_history(conn, DEMO_USER_ID)
    todays_insights = [i for i in all_insights if i["trade_date"] == latest_date] if latest_date else []
    return {"trade_date": latest_date, "insights": todays_insights}


@router.get("/holdings")
def holdings(conn: Connection = Depends(get_db)) -> list[dict]:
    held = watchlist_dal.get_portfolio(conn, DEMO_USER_ID)
    return [{**h, "snapshot": bars_dal.get_stock_snapshot(conn, h["symbol"])} for h in held]


@router.get("/insights/history")
def insights_history(symbol: str | None = None, conn: Connection = Depends(get_db)) -> list[dict]:
    return insights_dal.get_insights_history(conn, DEMO_USER_ID, symbol)


@router.post("/digest/run")
async def trigger_digest() -> list[dict]:
    resolve_insights(DEMO_USER_ID)
    return await run_daily_digest(DEMO_USER_ID)


@router.get("/digest/run-stream")
async def trigger_digest_stream() -> StreamingResponse:
    """Server-Sent Events: one {"message": "..."} line per resolution check
    (agent.resolve_insights.stream_resolve_insights) then per digest graph
    step (agent.digest_graph._progress_message), then {"done": true} or
    {"error": "..."} to end the stream. GET + native EventSource on the
    frontend, not POST — simpler than hand-rolling SSE-over-fetch parsing,
    and this trigger has no request body to send."""

    async def event_stream():
        try:
            for message in stream_resolve_insights(DEMO_USER_ID):
                yield f"data: {json.dumps({'message': message})}\n\n"
            async for message in stream_daily_digest(DEMO_USER_ID):
                yield f"data: {json.dumps({'message': message})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface to the client, don't just drop the connection
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
