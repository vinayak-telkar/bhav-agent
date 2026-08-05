"""
fo_daily queries — F&O positioning for the get_fo_positioning MCP tool.
Buildup classification follows the standard price/OI-change quadrant:
price up + OI up -> long buildup; price up + OI down -> short covering;
price down + OI up -> short buildup; price down + OI down -> long unwinding.
"""
import sqlite3


def get_fo_positioning(conn: sqlite3.Connection, symbol: str) -> dict | None:
    rows = conn.execute(
        """
        SELECT trade_date, fut_close, fut_oi, fut_oi_change, basis, pcr,
               max_call_oi_strike, max_put_oi_strike
        FROM fo_daily
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT 2
        """,
        (symbol,),
    ).fetchall()
    if not rows:
        return None

    today = rows[0]
    prior = rows[1] if len(rows) > 1 else None
    price_change = (
        today["fut_close"] - prior["fut_close"]
        if prior and today["fut_close"] is not None and prior["fut_close"] is not None
        else None
    )

    return {
        "symbol": symbol,
        "trade_date": today["trade_date"],
        "fut_close": today["fut_close"],
        "fut_oi": today["fut_oi"],
        "fut_oi_change": today["fut_oi_change"],
        "basis": today["basis"],
        "pcr": today["pcr"],
        "max_call_oi_strike": today["max_call_oi_strike"],
        "max_put_oi_strike": today["max_put_oi_strike"],
        "buildup_classification": _classify_buildup(price_change, today["fut_oi_change"]),
    }


def _classify_buildup(price_change: float | None, oi_change: int | None) -> str:
    if price_change is None or oi_change is None:
        return "insufficient_data"
    if price_change >= 0 and oi_change >= 0:
        return "long_buildup"
    if price_change >= 0 and oi_change < 0:
        return "short_covering"
    if price_change < 0 and oi_change >= 0:
        return "short_buildup"
    return "long_unwinding"
