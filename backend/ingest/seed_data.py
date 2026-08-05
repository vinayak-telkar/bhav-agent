"""
Synthetic data generator — a **test-fixture tool, not part of the app's real
setup flow**. Real usage starts empty: ingest real NSE data first (the
"Ingest Data" tab / ingest/local_ingest.py), then add real tickers via the
Manage screen — see README.md/TESTING.md. This module exists so the pytest
suite (DAL, MCP tools, digest graph) has a deterministic dataset that doesn't
depend on live NSE access or an API key; it is never run against the app's
real shared database.

**Do not use real NSE tickers here — this was a real bug, not a hypothetical
one.** An earlier version used RELIANCE/TCS/INFY/etc., and once a real
ingest touched the same shared demo database, upserts silently overwrote
those symbols' synthetic rows with real data for the overlapping date,
producing a fake ~59% "overnight drop" purely from the collision (see
specs/01's Changelog, 2026-08-03). All symbol names below are deliberately
fake (`DEMO*`) so this can't happen again, regardless of what DB this is
pointed at.

Five symbols chosen to exercise the PRD §4 signal table directly:
- DEMOACCUM (Category I): genuine accumulation — price up, delivery% rising.
- DEMORALLY (Category I): leveraged rally — price up, delivery% falling,
  futures long-OI ballooning, steep basis.
- DEMODIST (Category I): quiet distribution — price flat, delivery% high &
  rising, volume elevated.
- DEMOSMALL (Category II, no F&O): speculative churn — one low-delivery
  volume spike, otherwise unremarkable.
- DEMOBE (Category II): one corporate-action-flagged discontinuity, then a
  late move from EQ to BE series (exit-liquidity-deterioration signal).

Deterministic (seeded RNG) so re-running produces identical rows — reused by
DAL/MCP unit tests, not just manual poking.
"""
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEMO_USER_ID  # noqa: E402
from app.data import db, symbols as symbols_dal, watchlist as watchlist_dal  # noqa: E402

TRADING_DAYS = 25


def _trading_days(end_date: date, count: int) -> list[date]:
    """Last `count` weekdays ending on or before end_date, oldest first."""
    days = []
    d = end_date
    while len(days) < count:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


@dataclass
class SymbolSpec:
    symbol: str
    name: str
    category_one: bool  # F&O-eligible -> closing_price_method='cas_auction'
    close_start: float
    close_end: float
    deliv_pct_start: float
    deliv_pct_end: float
    volume_base: int
    volatility: float = 0.01
    corporate_action_day_index: int | None = None  # index into the day list
    series_change_from_index: int | None = None  # switches to 'BE' from this index
    volume_spike_day_index: int | None = None
    seed: int = 0


SYMBOL_SPECS = [
    SymbolSpec(
        symbol="DEMOACCUM", name="Demo Accumulation Co Ltd", category_one=True,
        close_start=2800.0, close_end=3220.0,
        deliv_pct_start=36.0, deliv_pct_end=66.0,
        volume_base=6_000_000, seed=1,
    ),
    SymbolSpec(
        symbol="DEMORALLY", name="Demo Leveraged Rally Co Ltd", category_one=True,
        close_start=3600.0, close_end=4050.0,
        deliv_pct_start=55.0, deliv_pct_end=29.0,
        volume_base=3_000_000, seed=2,
    ),
    SymbolSpec(
        symbol="DEMODIST", name="Demo Distribution Co Ltd", category_one=True,
        close_start=1500.0, close_end=1515.0,
        deliv_pct_start=58.0, deliv_pct_end=76.0,
        volume_base=4_500_000, seed=3,
    ),
    SymbolSpec(
        symbol="DEMOSMALL", name="Demo Smallcap Speculative Co Ltd", category_one=False,
        close_start=85.0, close_end=88.0,
        deliv_pct_start=42.0, deliv_pct_end=40.0,
        volume_base=400_000, volume_spike_day_index=18, seed=4,
    ),
    SymbolSpec(
        symbol="DEMOBE", name="Demo BE-Alert Industries Ltd", category_one=False,
        close_start=210.0, close_end=150.0,
        deliv_pct_start=48.0, deliv_pct_end=52.0,
        volume_base=250_000,
        corporate_action_day_index=10,
        series_change_from_index=20,
        seed=5,
    ),
]


def _interpolate(start: float, end: float, i: int, n: int) -> float:
    return start + (end - start) * (i / (n - 1))


def _generate_bars(spec: SymbolSpec, trade_dates: list[date]) -> list[dict]:
    rng = random.Random(spec.seed)
    n = len(trade_dates)
    closing_price_method = "cas_auction" if spec.category_one else "vwap_30min"
    rows = []
    prev_close = spec.close_start * (1 - spec.volatility)

    for i, d in enumerate(trade_dates):
        close = _interpolate(spec.close_start, spec.close_end, i, n) * (
            1 + rng.uniform(-spec.volatility, spec.volatility)
        )
        deliv_pct = _interpolate(spec.deliv_pct_start, spec.deliv_pct_end, i, n) + rng.uniform(-2, 2)
        deliv_pct = max(1.0, min(99.0, deliv_pct))

        volume = int(spec.volume_base * (1 + rng.uniform(-0.15, 0.15)))
        if spec.volume_spike_day_index == i:
            volume *= 5
            deliv_pct = min(deliv_pct, 15.0)  # speculative churn: spike + low delivery

        corporate_action_flag = 0
        if spec.corporate_action_day_index == i:
            corporate_action_flag = 1
            close = close / 2  # simulate a 1:2 split discontinuity

        series = "EQ"
        if spec.series_change_from_index is not None and i >= spec.series_change_from_index:
            series = "BE"

        open_ = prev_close * (1 + rng.uniform(-0.005, 0.005))
        high = max(open_, close) * (1 + rng.uniform(0, 0.01))
        low = min(open_, close) * (1 - rng.uniform(0, 0.01))
        vwap = (open_ + high + low + close) / 4
        trades = max(1, int(volume / rng.uniform(80, 400)))
        turnover = volume * vwap
        deliv_qty = int(volume * deliv_pct / 100)

        rows.append(
            {
                "symbol": spec.symbol,
                "trade_date": d.isoformat(),
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "prev_close": round(prev_close, 2),
                "vwap": round(vwap, 2),
                "volume": volume,
                "turnover": round(turnover, 2),
                "trades": trades,
                "series": series,
                "deliv_qty": deliv_qty,
                "deliv_pct": round(deliv_pct, 2),
                "corporate_action_flag": corporate_action_flag,
                "closing_price_method": closing_price_method,
            }
        )
        prev_close = close

    return rows


def _generate_fo(spec: SymbolSpec, bars: list[dict]) -> list[dict]:
    """Only for Category I symbols. DEMORALLY gets a deliberately steep
    long-OI buildup + basis to fire the "leveraged rally" signature end to end."""
    if not spec.category_one:
        return []

    rng = random.Random(spec.seed + 100)
    rows = []
    oi = 20_000_000
    for i, bar in enumerate(bars):
        fut_close = bar["close"] * (1 + rng.uniform(-0.002, 0.006))
        if spec.symbol == "DEMORALLY":
            oi_change = int(oi * rng.uniform(0.03, 0.06))  # ballooning long OI
            basis = fut_close - bar["close"] + rng.uniform(8, 15)  # steep basis
        else:
            oi_change = int(oi * rng.uniform(-0.02, 0.02))
            basis = fut_close - bar["close"] + rng.uniform(-2, 2)
        oi += oi_change
        pcr = round(rng.uniform(0.7, 1.3), 2)
        rows.append(
            {
                "symbol": spec.symbol,
                "trade_date": bar["trade_date"],
                "fut_close": round(fut_close, 2),
                "fut_oi": oi,
                "fut_oi_change": oi_change,
                "basis": round(basis, 2),
                "pcr": pcr,
                "max_call_oi_strike": round(bar["close"] * 1.05, 1),
                "max_put_oi_strike": round(bar["close"] * 0.95, 1),
            }
        )
    return rows


def seed(db_path: str | None = None, end_date: date | None = None) -> None:
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        end_date = end_date or date.today()
        trade_dates = _trading_days(end_date, TRADING_DAYS)
        now = datetime.now(timezone.utc).isoformat()

        for spec in SYMBOL_SPECS:
            symbols_dal.upsert_symbol(
                conn,
                symbol=spec.symbol,
                name=spec.name,
                isin=None,
                series="EQ",
                listing_date=None,
                last_updated=now,
            )

            bars = _generate_bars(spec, trade_dates)
            conn.executemany(
                """
                INSERT INTO daily_bars (
                    symbol, trade_date, open, high, low, close, prev_close, vwap,
                    volume, turnover, trades, series, deliv_qty, deliv_pct,
                    corporate_action_flag, closing_price_method
                ) VALUES (
                    :symbol, :trade_date, :open, :high, :low, :close, :prev_close, :vwap,
                    :volume, :turnover, :trades, :series, :deliv_qty, :deliv_pct,
                    :corporate_action_flag, :closing_price_method
                )
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, prev_close=excluded.prev_close, vwap=excluded.vwap,
                    volume=excluded.volume, turnover=excluded.turnover, trades=excluded.trades,
                    series=excluded.series, deliv_qty=excluded.deliv_qty, deliv_pct=excluded.deliv_pct,
                    corporate_action_flag=excluded.corporate_action_flag,
                    closing_price_method=excluded.closing_price_method
                """,
                bars,
            )

            fo_rows = _generate_fo(spec, bars)
            if fo_rows:
                conn.executemany(
                    """
                    INSERT INTO fo_daily (
                        symbol, trade_date, fut_close, fut_oi, fut_oi_change, basis,
                        pcr, max_call_oi_strike, max_put_oi_strike
                    ) VALUES (
                        :symbol, :trade_date, :fut_close, :fut_oi, :fut_oi_change, :basis,
                        :pcr, :max_call_oi_strike, :max_put_oi_strike
                    )
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET
                        fut_close=excluded.fut_close, fut_oi=excluded.fut_oi,
                        fut_oi_change=excluded.fut_oi_change, basis=excluded.basis,
                        pcr=excluded.pcr, max_call_oi_strike=excluded.max_call_oi_strike,
                        max_put_oi_strike=excluded.max_put_oi_strike
                    """,
                    fo_rows,
                )

        for d in trade_dates:
            conn.execute(
                "INSERT OR IGNORE INTO market_days (trade_date, is_trading_day, note) VALUES (?, 1, NULL)",
                (d.isoformat(),),
            )

        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (DEMO_USER_ID, now),
        )
        conn.commit()

        watchlist_dal.add_watch(conn, DEMO_USER_ID, "DEMOACCUM", "held", "large")
        watchlist_dal.add_watch(conn, DEMO_USER_ID, "DEMORALLY", "held", "medium")
        watchlist_dal.add_watch(conn, DEMO_USER_ID, "DEMOBE", "held", "small")
        watchlist_dal.add_watch(conn, DEMO_USER_ID, "DEMODIST", "wishlist")
        watchlist_dal.add_watch(conn, DEMO_USER_ID, "DEMOSMALL", "wishlist")
    finally:
        conn.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    seed(target)
    print(f"Seeded {'default DB path' if target is None else target} with {len(SYMBOL_SPECS)} symbols, "
          f"{TRADING_DAYS} trading days, demo user '{DEMO_USER_ID}'.")
