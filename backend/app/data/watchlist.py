"""
watchlist DAL — held/wishlist symbols per user. A symbol is held or wished,
never both (PK is (user_id, symbol)); "I bought it" promotion is a one-line
UPDATE. CHECK constraints in schema.sql can't cross-validate status vs
size_bucket, so that rule is enforced here (spec 03 acceptance criteria).
"""
import sqlite3
from datetime import datetime, timezone


class ValidationError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_status_bucket(status: str, size_bucket: str | None) -> None:
    if status == "held" and size_bucket is None:
        raise ValidationError("held positions require a size_bucket")
    if status == "wishlist" and size_bucket is not None:
        raise ValidationError("wishlist entries must not carry a size_bucket")


def get_watchlist(conn: sqlite3.Connection, user_id: str, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_id = ? ORDER BY symbol", (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_id = ? AND status = ? ORDER BY symbol",
            (user_id, status),
        ).fetchall()
    return [dict(r) for r in rows]


def get_portfolio(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    return get_watchlist(conn, user_id, status="held")


def get_wishlist(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    return get_watchlist(conn, user_id, status="wishlist")


def add_watch(
    conn: sqlite3.Connection,
    user_id: str,
    symbol: str,
    status: str,
    size_bucket: str | None = None,
) -> dict:
    _validate_status_bucket(status, size_bucket)
    added_at = _now_iso()
    conn.execute(
        """
        INSERT INTO watchlist (user_id, symbol, status, size_bucket, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (user_id, symbol) DO UPDATE SET
            status = excluded.status,
            size_bucket = excluded.size_bucket
        """,
        (user_id, symbol, status, size_bucket, added_at),
    )
    conn.commit()
    return {"user_id": user_id, "symbol": symbol, "status": status, "size_bucket": size_bucket, "added_at": added_at}


def update_watch(
    conn: sqlite3.Connection,
    user_id: str,
    symbol: str,
    *,
    status: str | None = None,
    size_bucket: str | None = None,
) -> dict:
    existing = conn.execute(
        "SELECT * FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol)
    ).fetchone()
    if existing is None:
        raise ValidationError(f"no watchlist entry for {user_id}/{symbol}")

    new_status = status if status is not None else existing["status"]
    new_bucket = size_bucket if size_bucket is not None else existing["size_bucket"]
    _validate_status_bucket(new_status, new_bucket)

    conn.execute(
        "UPDATE watchlist SET status = ?, size_bucket = ? WHERE user_id = ? AND symbol = ?",
        (new_status, new_bucket, user_id, symbol),
    )
    conn.commit()
    return {"user_id": user_id, "symbol": symbol, "status": new_status, "size_bucket": new_bucket}


def promote_to_held(conn: sqlite3.Connection, user_id: str, symbol: str, size_bucket: str) -> dict:
    """"I bought it" — promotes a wishlist row to held. Thin wrapper over
    update_watch for callers that want the promotion's intent to read clearly."""
    return update_watch(conn, user_id, symbol, status="held", size_bucket=size_bucket)


def remove_watch(conn: sqlite3.Connection, user_id: str, symbol: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    conn.commit()
