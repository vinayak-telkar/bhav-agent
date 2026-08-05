"""
daily_bars queries + baseline windows. Baselines are computed on read via
window functions (tech spec §3), never stored — every baseline query filters
corporate_action_flag = 0 so a split/bonus day never pollutes a baseline
(spec 03 acceptance criteria: this filter was missing from the tech spec's
example query and must be added here).

Consumed directly by mcp_server/server.py's get_stock_snapshot and
get_delivery_trend tools — see specs/06 (not yet written) for the tool
contract; this module is the DAL those tools are a thin wrapper over.
"""
import sqlite3


def get_latest_trade_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) AS d FROM daily_bars").fetchone()
    return row["d"] if row else None


def get_stock_snapshot(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """Today's OHLC + close-vs-VWAP, volume-vs-20d-avg, delivery-vs-20d-baseline,
    turnover-rank delta, all computed against the trailing 20 sessions
    (ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), excluding corporate-action-flagged
    rows from the baseline window.
    """
    row = conn.execute(
        """
        SELECT
            trade_date, open, high, low, close, prev_close, vwap, volume, turnover,
            deliv_qty, deliv_pct, series, corporate_action_flag, closing_price_method,
            AVG(volume) OVER w   AS volume_20d_avg,
            AVG(deliv_pct) OVER w AS deliv_pct_20d_avg
        FROM daily_bars
        WHERE symbol = ?
        WINDOW w AS (
            ORDER BY trade_date
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        )
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return None

    result = dict(row)
    result["turnover_rank_today"] = _turnover_rank(conn, symbol, row["trade_date"])
    return result


def _turnover_rank(conn: sqlite3.Connection, symbol: str, trade_date: str) -> int | None:
    """1-indexed rank of symbol's turnover among all symbols traded that day
    (1 = highest turnover). None if the symbol has no turnover value that day.
    """
    row = conn.execute(
        """
        SELECT rank FROM (
            SELECT symbol, RANK() OVER (ORDER BY turnover DESC) AS rank
            FROM daily_bars
            WHERE trade_date = ? AND turnover IS NOT NULL
        )
        WHERE symbol = ?
        """,
        (trade_date, symbol),
    ).fetchone()
    return row["rank"] if row else None


def get_delivery_trend(conn: sqlite3.Connection, symbol: str, days: int = 20) -> dict | None:
    """Rolling delivery % series, deliv-qty trend, avg-trade-size trend over the
    trailing `days` sessions (excluding corporate-action-flagged rows).
    """
    rows = conn.execute(
        """
        SELECT trade_date, deliv_pct, deliv_qty, volume, trades
        FROM daily_bars
        WHERE symbol = ? AND corporate_action_flag = 0
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (symbol, days),
    ).fetchall()
    if not rows:
        return None

    rows = list(reversed(rows))  # oldest -> newest for trend readability
    deliv_pct_series = [r["deliv_pct"] for r in rows]
    deliv_qty_series = [r["deliv_qty"] for r in rows]
    avg_trade_size_series = [
        (r["volume"] / r["trades"]) if r["trades"] else None for r in rows
    ]

    return {
        "symbol": symbol,
        "trade_dates": [r["trade_date"] for r in rows],
        "deliv_pct_series": deliv_pct_series,
        "deliv_qty_series": deliv_qty_series,
        "avg_trade_size_series": avg_trade_size_series,
        "deliv_qty_trend": _trend_label(deliv_qty_series),
        "avg_trade_size_trend": _trend_label(avg_trade_size_series),
    }


def _trend_label(series: list) -> str:
    """Simple first-half-vs-second-half average comparison — cheap, deterministic,
    and readable in evidence output. Not a statistical trend test; that precision
    isn't needed for a "rising/falling/flat" label the model quotes verbatim.
    """
    clean = [v for v in series if v is not None]
    if len(clean) < 4:
        return "insufficient_data"
    mid = len(clean) // 2
    first_half_avg = sum(clean[:mid]) / mid
    second_half_avg = sum(clean[mid:]) / (len(clean) - mid)
    if first_half_avg == 0:
        return "insufficient_data"
    pct_change = (second_half_avg - first_half_avg) / first_half_avg
    if pct_change > 0.05:
        return "rising"
    if pct_change < -0.05:
        return "falling"
    return "flat"
