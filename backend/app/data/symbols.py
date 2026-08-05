"""
symbols DAL — reference data upserted by ingest, read by the /symbols
autocomplete route and by watchlist add (FK integrity: a symbol must exist
here before it can be watched).
"""
import sqlite3


def upsert_symbol(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    name: str,
    isin: str | None,
    series: str,
    listing_date: str | None,
    last_updated: str,
) -> None:
    conn.execute(
        """
        INSERT INTO symbols (symbol, name, isin, series, is_active, listing_date, last_updated)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            name = excluded.name,
            isin = excluded.isin,
            series = excluded.series,
            is_active = 1,
            listing_date = COALESCE(excluded.listing_date, symbols.listing_date),
            last_updated = excluded.last_updated
        """,
        (symbol, name, isin, series, listing_date, last_updated),
    )


def search_symbols(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Prefix + substring match on symbol or name, active symbols only."""
    like = f"%{query.upper()}%"
    rows = conn.execute(
        """
        SELECT symbol, name, series FROM symbols
        WHERE is_active = 1 AND (symbol LIKE ? OR UPPER(name) LIKE ?)
        ORDER BY
            CASE WHEN symbol LIKE ? THEN 0 ELSE 1 END,
            symbol
        LIMIT ?
        """,
        (like, like, f"{query.upper()}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_symbol(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute("SELECT * FROM symbols WHERE symbol = ?", (symbol,)).fetchone()
    return dict(row) if row else None
