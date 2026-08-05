"""
FastMCP server. Tools import app.data.* — thin wrappers, no duplicated queries.
Five core tools: get_stock_snapshot, get_delivery_trend, get_fo_positioning,
get_portfolio/get_wishlist, get_prior_insights/save_insight.
See TECH_SPEC.md §5 and specs/06_mcp_server_tools.md.

Transport: stdio only (tech spec §5) — both agents spawn this as a subprocess
via MultiServerMCPClient; no ports opened.

Each tool opens its own short-lived DB connection rather than sharing one
across calls — sqlite3.Connection isn't safe to share across concurrent
async tool invocations, and a per-call connection is cheap enough for this
project's traffic (tech spec §7: single demo user, no auth, low volume).
"""
import logging
from datetime import date

from app.data import bars as bars_dal
from app.data import db as db_module
from app.data import fo as fo_dal
from app.data import insights as insights_dal
from app.data import watchlist as watchlist_dal
from ingest import local_ingest

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "mcp package not installed or wrong version — see pyproject.toml pin "
        "(mcp==1.29.0; mcp==2.0.0 breaks langchain-mcp-adapters, spec 02)"
    ) from exc

# The mcp SDK logs "Processing request of type X" at INFO for every single
# tool call (mcp/server/lowlevel/server.py) — this subprocess's stderr is
# inherited by whatever spawned it (the FastAPI backend), so every digest run
# floods that console with this noise. Not useful signal for anyone watching
# the backend terminal — real progress now has its own purpose-built channel
# (agent.digest_graph.stream_daily_digest, GET /digest/run-stream). Quiet it
# to WARNING so only real problems surface.
logging.getLogger("mcp").setLevel(logging.WARNING)

mcp = FastMCP("bhav-agent")


@mcp.tool()
def get_stock_snapshot(symbol: str) -> dict:
    """Today's OHLC, close-vs-VWAP, volume-vs-20d-avg, delivery-vs-20d-baseline,
    and turnover-rank for a symbol. Returns {"error": ...} if the symbol has
    no daily_bars data."""
    conn = db_module.get_connection()
    try:
        snapshot = bars_dal.get_stock_snapshot(conn, symbol)
        if snapshot is None:
            return {"error": f"no daily_bars data for symbol '{symbol}'"}
        return snapshot
    finally:
        conn.close()


@mcp.tool()
def get_delivery_trend(symbol: str, days: int = 20) -> dict:
    """Rolling delivery %, deliv-qty trend, and avg-trade-size trend over the
    trailing `days` sessions for a symbol."""
    conn = db_module.get_connection()
    try:
        trend = bars_dal.get_delivery_trend(conn, symbol, days=days)
        if trend is None:
            return {"error": f"no daily_bars data for symbol '{symbol}'"}
        return trend
    finally:
        conn.close()


@mcp.tool()
def get_fo_positioning(symbol: str) -> dict:
    """F&O buildup classification (long/short buildup, covering, unwinding),
    basis, PCR, and max call/put OI strikes for a symbol. Returns
    {"error": ...} if the symbol has no F&O data (i.e. it's not
    F&O-eligible / Category II)."""
    conn = db_module.get_connection()
    try:
        positioning = fo_dal.get_fo_positioning(conn, symbol)
        if positioning is None:
            return {"error": f"no F&O data for symbol '{symbol}' (likely not F&O-eligible)"}
        return positioning
    finally:
        conn.close()


@mcp.tool()
def get_portfolio(user_id: str) -> list[dict]:
    """Held symbols for a user, with size buckets."""
    conn = db_module.get_connection()
    try:
        return watchlist_dal.get_portfolio(conn, user_id)
    finally:
        conn.close()


@mcp.tool()
def get_wishlist(user_id: str) -> list[dict]:
    """Wishlist symbols for a user."""
    conn = db_module.get_connection()
    try:
        return watchlist_dal.get_wishlist(conn, user_id)
    finally:
        conn.close()


@mcp.tool()
def get_prior_insights(user_id: str, symbol: str, limit: int = 5) -> list[dict]:
    """Most recent past insights for this user/symbol, newest first — cross-day
    memory the digest graph reconciles today's read against."""
    conn = db_module.get_connection()
    try:
        return insights_dal.get_prior_insights(conn, user_id, symbol, limit=limit)
    finally:
        conn.close()


@mcp.tool()
def save_insight(
    user_id: str,
    symbol: str,
    trade_date: str,
    signal_type: str,
    action: str,
    confidence: str,
    narrative: str,
    evidence: dict,
    price_at_insight: float,
) -> dict:
    """Persists a new insight (status starts 'pending'). Not loaded into
    chat's read-only tool allowlist (tech spec §6b) — digest-only."""
    conn = db_module.get_connection()
    try:
        insight_id = insights_dal.save_insight(
            conn,
            user_id=user_id,
            symbol=symbol,
            trade_date=trade_date,
            signal_type=signal_type,
            action=action,
            confidence=confidence,
            narrative=narrative,
            evidence=evidence,
            price_at_insight=price_at_insight,
        )
        return {"id": insight_id, "status": "saved"}
    finally:
        conn.close()


@mcp.tool()
def ingest_local_bhavcopy(
    trade_date: str,
    cash_file: str,
    delivery_file: str | None = None,
    fo_file: str | None = None,
) -> dict:
    """Parses and loads already-downloaded NSE bhavcopy files (the user
    downloaded them via their own browser — NSE's Akamai bot-detection blocks
    scripted downloads, see ingest/bhavcopy.py's module docstring). Reuses the
    exact same parse+load logic as the automated network path. `trade_date`
    is an ISO 'YYYY-MM-DD' string. Returns status/symbols_loaded/
    corporate_actions_flagged/error — see ingest.bhavcopy.IngestResult."""
    result = local_ingest.ingest_from_local_files(
        date.fromisoformat(trade_date), cash_file, delivery_file, fo_file
    )
    return {
        "trade_date": result.trade_date.isoformat(),
        "status": result.status,
        "symbols_loaded": result.symbols_loaded,
        "corporate_actions_flagged": result.corporate_actions_flagged,
        "error": result.error,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
