"""
NSE bhavcopy ingest. ingest_date(trade_date) -> IngestResult, backfill(start, end).
Downloads cash UDiFF bhavcopy, sec_bhavdata_full (delivery), F&O bhavcopy.
Idempotent, skips non-trading days, flags corporate actions, records
closing_price_method per SEBI's Closing Auction Session (2026-08-03).
See TECH_SPEC.md §4 and specs/01_ingest_bhavcopy.md.

**Live-access caveat:** this build environment's outbound requests to nseindia.com
return HTTP 403 from Akamai's bot-detection before any parsing logic even runs —
confirmed even from a normal home network, not just a sandbox (specs/01's Changelog).
`ingest/local_ingest.py` is the practical workaround: the user downloads files via their
own real browser (which passes Akamai fine) and that module locates + loads them,
reusing this module's exact parse/load logic (`_process_and_load`).

**URL patterns below are now confirmed correct against live NSE downloads** (2026-08-03,
via a real user's browser — see specs/01's Changelog) — all three matched real files
NSE actually serves. The one live gotcha found: a given date's file may 404 simply
because NSE hasn't published it yet (cash bhavcopy is typically out by ~18:30 IST, F&O
sometimes later), not because the URL pattern is wrong — don't assume a 404 means the
pattern broke; try an earlier date or wait. Column layouts in _parse_cash_udiff /
_parse_delivery_file / _parse_fo_udiff remain unverified against a real file's exact
columns — if parsing fails after a successful download, check those first.
`ingest/seed_data.py` is still the tested path every other component in this project is
built and tested against — see TECH_SPEC.md §11.
"""
import csv
import io
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import db, symbols as symbols_dal  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"

# Confirmed correct against live NSE downloads (2026-08-03) — see module docstring
# and specs/01's Changelog. A 404 on a specific date usually means that date's file
# isn't published yet, not that the pattern is wrong.
CASH_UDIFF_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
DELIVERY_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
FO_UDIFF_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Corporate-action heuristic: an overnight close discontinuity beyond this
# fraction, co-occurring with a volume anomaly, is flagged (spec 01: N to be
# tuned once real data is in hand — 15% is a starting point wide enough to
# not trip on ordinary volatility but narrow enough to catch a 1:2 split).
CORPORATE_ACTION_CLOSE_JUMP_THRESHOLD = 0.15


@dataclass
class IngestResult:
    trade_date: date
    status: Literal["ok", "skipped_non_trading_day", "skipped_already_ingested", "failed"]
    symbols_loaded: int = 0
    corporate_actions_flagged: list[str] = field(default_factory=list)
    error: str | None = None


def _warm_up_session() -> httpx.Client:
    """NSE requires a homepage visit to set cookies before archive endpoints
    will respond to a script (documented anti-scraping behavior). A plain
    `curl` from this sandbox got a 403 on the homepage itself, before cookies
    ever mattered — see module docstring. This warm-up is still the correct
    approach for a network where NSE isn't blocking the IP outright."""
    client = httpx.Client(headers=BROWSER_HEADERS, timeout=30.0, follow_redirects=True)
    client.get("https://www.nseindia.com")
    return client


def _download_with_retry(client: httpx.Client, url: str, max_retries: int = 3) -> bytes | None:
    """Returns the response body, None on 404 (treated as non-trading day),
    raises after exhausting retries on other errors. Backs off on 403/429."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.get(url)
            if resp.status_code == 404:
                return None
            if resp.status_code in (403, 429):
                time.sleep(2**attempt)
                last_error = RuntimeError(f"{resp.status_code} from {url}")
                continue
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url} after {max_retries} attempts") from last_error


def _extract_csv_text(content: bytes) -> str:
    """UDiFF files are shipped as a zip containing one CSV; sec_bhavdata_full
    is plain CSV. Detect by zip magic bytes rather than trusting the URL's
    extension, since NSE has switched compression on/off across format
    revisions before."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            name = zf.namelist()[0]
            return zf.read(name).decode("utf-8")
    return content.decode("utf-8")


def _parse_cash_udiff(csv_text: str, trade_date: date) -> dict[str, dict]:
    """Cash-segment rows only (Sgmt == 'CM'). Returns {symbol: partial daily_bars row}
    — deliv_qty/deliv_pct filled in later from the delivery file."""
    # skipinitialspace=True: harmless here (this UDiFF file uses a clean "," delimiter,
    # confirmed against a real download) — see _parse_delivery_file for why it's
    # required, not just defensive, on that file.
    reader = csv.DictReader(io.StringIO(csv_text), skipinitialspace=True)
    rows: dict[str, dict] = {}
    for r in reader:
        if r.get("Sgmt", "").strip().upper() != "CM":
            continue
        symbol = r["TckrSymb"].strip()
        rows[symbol] = {
            "symbol": symbol,
            "trade_date": trade_date.isoformat(),
            "open": float(r["OpnPric"]),
            "high": float(r["HghPric"]),
            "low": float(r["LwPric"]),
            "close": float(r["ClsPric"]),
            "prev_close": float(r["PrvsClsgPric"]) if r.get("PrvsClsgPric") else None,
            "vwap": None,
            "volume": int(float(r["TtlTradgVol"])),
            "turnover": float(r["TtlTrfVal"]) if r.get("TtlTrfVal") else None,
            "trades": int(r["TtlNbOfTxsExctd"]) if r.get("TtlNbOfTxsExctd") else None,
            "series": r.get("SctySrs", "EQ").strip(),
        }
    return rows


def _parse_delivery_file(csv_text: str) -> dict[str, dict]:
    """Returns {symbol: {"deliv_qty": int, "deliv_pct": float, "vwap": float}}."""
    # skipinitialspace=True: NSE's sec_bhavdata_full delivery CSV uses ", " (comma +
    # space) as its field separator, not a bare ",". Without this, csv.DictReader
    # treats the leading space as part of every column name after the first (e.g. the
    # real key is " SERIES", not "SERIES"), which silently breaks every .get() lookup
    # instead of raising — confirmed against a real downloaded file (2026-08-03); every
    # row was being skipped and deliv_qty/deliv_pct silently came back None for every
    # symbol. The cash/F&O UDiFF files don't have this issue (clean "," delimiter,
    # confirmed against a real file too) but the flag is harmless there.
    reader = csv.DictReader(io.StringIO(csv_text), skipinitialspace=True)
    out = {}
    for r in reader:
        symbol = r["SYMBOL"].strip()
        series = r.get("SERIES", "").strip()
        if series != "EQ":
            continue  # delivery file carries all series; EQ is the primary listing
        out[symbol] = {
            "deliv_qty": int(float(r["DELIV_QTY"])) if r.get("DELIV_QTY", "-").strip() != "-" else None,
            "deliv_pct": float(r["DELIV_PER"]) if r.get("DELIV_PER", "-").strip() != "-" else None,
            "vwap": float(r["AVG_PRICE"]) if r.get("AVG_PRICE") else None,
        }
    return out


def _parse_fo_udiff(csv_text: str, trade_date: date) -> dict[str, dict]:
    """Rolls up FUTSTK contracts per underlying into one fo_daily row.
    Options aggregation for PCR/max-OI-strike uses OPTSTK rows for the same
    underlying's nearest expiry."""
    # skipinitialspace=True: harmless here (this UDiFF file uses a clean "," delimiter,
    # confirmed against a real download) — see _parse_delivery_file for why it's
    # required, not just defensive, on that file.
    reader = csv.DictReader(io.StringIO(csv_text), skipinitialspace=True)
    futures: dict[str, dict] = {}
    options: dict[str, list[dict]] = {}

    for r in reader:
        instr = r.get("FinInstrmTp", "").strip().upper()
        underlying = r.get("TckrSymb", "").strip()
        if instr == "STF":  # stock futures
            existing = futures.get(underlying)
            oi = int(float(r["OpnIntrst"])) if r.get("OpnIntrst") else 0
            if existing is None or r.get("XpryDt", "") < existing["_expiry"]:
                futures[underlying] = {
                    "fut_close": float(r["ClsPric"]),
                    "fut_oi": oi,
                    "fut_oi_change": int(float(r["ChngInOpnIntrst"])) if r.get("ChngInOpnIntrst") else 0,
                    "_expiry": r.get("XpryDt", ""),
                }
        elif instr == "STO":  # stock options
            options.setdefault(underlying, []).append(r)

    result = {}
    for underlying, fut in futures.items():
        opt_rows = options.get(underlying, [])
        call_oi = {r["StrkPric"]: int(float(r["OpnIntrst"] or 0)) for r in opt_rows if r.get("OptnTp") == "CE"}
        put_oi = {r["StrkPric"]: int(float(r["OpnIntrst"] or 0)) for r in opt_rows if r.get("OptnTp") == "PE"}
        total_call_oi = sum(call_oi.values())
        total_put_oi = sum(put_oi.values())
        result[underlying] = {
            "symbol": underlying,
            "trade_date": trade_date.isoformat(),
            "fut_close": fut["fut_close"],
            "fut_oi": fut["fut_oi"],
            "fut_oi_change": fut["fut_oi_change"],
            "basis": None,  # filled in once merged with cash close
            "pcr": round(total_put_oi / total_call_oi, 4) if total_call_oi else None,
            "max_call_oi_strike": float(max(call_oi, key=call_oi.get)) if call_oi else None,
            "max_put_oi_strike": float(max(put_oi, key=put_oi.get)) if put_oi else None,
        }
    return result


def _detect_corporate_actions(rows: dict[str, dict]) -> list[str]:
    """Flags symbols whose close moved more than the threshold overnight
    while volume also spiked — heuristic only, no ratio classification
    (spec 01: out of scope)."""
    flagged = []
    for symbol, row in rows.items():
        prev_close = row.get("prev_close")
        if not prev_close:
            continue
        jump = abs(row["close"] - prev_close) / prev_close
        if jump >= CORPORATE_ACTION_CLOSE_JUMP_THRESHOLD:
            flagged.append(symbol)
    return flagged


def ingest_date(trade_date: date, db_path: str | None = None) -> IngestResult:
    """Downloads + parses cash UDiFF bhavcopy, sec_bhavdata_full (delivery), and
    F&O bhavcopy for trade_date. Idempotent: safe to re-run for a date already
    ingested (upsert, not insert)."""
    conn = db.get_connection(db_path)
    try:
        yyyymmdd = trade_date.strftime("%Y%m%d")
        ddmmyyyy = trade_date.strftime("%d%m%Y")

        client = _warm_up_session()
        try:
            cash_content = _download_with_retry(client, CASH_UDIFF_URL.format(yyyymmdd=yyyymmdd))
            if cash_content is None:
                conn.execute(
                    "INSERT OR REPLACE INTO market_days (trade_date, is_trading_day, note) VALUES (?, 0, 'no cash bhavcopy file')",
                    (trade_date.isoformat(),),
                )
                conn.commit()
                return IngestResult(trade_date, "skipped_non_trading_day")

            delivery_content = _download_with_retry(client, DELIVERY_URL.format(ddmmyyyy=ddmmyyyy))
            fo_content = _download_with_retry(client, FO_UDIFF_URL.format(yyyymmdd=yyyymmdd))
        finally:
            client.close()

        return _process_and_load(conn, trade_date, cash_content, delivery_content, fo_content)
    except Exception as exc:  # noqa: BLE001 — ingest must report, not crash the run
        return IngestResult(trade_date, "failed", error=str(exc))
    finally:
        conn.close()


def _process_and_load(
    conn,
    trade_date: date,
    cash_content: bytes,
    delivery_content: bytes | None,
    fo_content: bytes | None,
) -> IngestResult:
    """Parse + merge + upsert, given raw file bytes from *any* source (network
    download or a locally-downloaded file — see ingest/local_ingest.py). This
    is the one place that logic lives; both ingest paths call it so they can
    never drift apart.
    """
    already = conn.execute(
        "SELECT 1 FROM daily_bars WHERE trade_date = ? LIMIT 1", (trade_date.isoformat(),)
    ).fetchone()

    _archive_raw(trade_date, cash_content, delivery_content, fo_content)

    bars = _parse_cash_udiff(_extract_csv_text(cash_content), trade_date)
    delivery = _parse_delivery_file(_extract_csv_text(delivery_content)) if delivery_content else {}
    fo_rows = _parse_fo_udiff(_extract_csv_text(fo_content), trade_date) if fo_content else {}

    category_one_symbols = set(fo_rows.keys())
    for symbol, row in bars.items():
        deliv = delivery.get(symbol, {})
        row["deliv_qty"] = deliv.get("deliv_qty")
        row["deliv_pct"] = deliv.get("deliv_pct")
        row["vwap"] = deliv.get("vwap") or row["close"]
        row["closing_price_method"] = "cas_auction" if symbol in category_one_symbols else "vwap_30min"

    corporate_actions = _detect_corporate_actions(bars)
    for symbol in corporate_actions:
        bars[symbol]["corporate_action_flag"] = 1
    for symbol, row in bars.items():
        row.setdefault("corporate_action_flag", 0)

    for symbol, fo_row in fo_rows.items():
        cash_close = bars.get(symbol, {}).get("close")
        if cash_close and fo_row["fut_close"] is not None:
            fo_row["basis"] = round(fo_row["fut_close"] - cash_close, 4)

    for symbol, row in bars.items():
        symbols_dal.upsert_symbol(
            conn, symbol=symbol, name=symbol, isin=None, series=row["series"],
            listing_date=None, last_updated=trade_date.isoformat(),
        )
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
            open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
            prev_close=excluded.prev_close, vwap=excluded.vwap, volume=excluded.volume,
            turnover=excluded.turnover, trades=excluded.trades, series=excluded.series,
            deliv_qty=excluded.deliv_qty, deliv_pct=excluded.deliv_pct,
            corporate_action_flag=excluded.corporate_action_flag,
            closing_price_method=excluded.closing_price_method
        """,
        list(bars.values()),
    )
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
            list(fo_rows.values()),
        )
    conn.execute(
        "INSERT OR REPLACE INTO market_days (trade_date, is_trading_day, note) VALUES (?, 1, NULL)",
        (trade_date.isoformat(),),
    )
    conn.commit()

    return IngestResult(
        trade_date,
        "skipped_already_ingested" if already else "ok",
        symbols_loaded=len(bars),
        corporate_actions_flagged=corporate_actions,
    )


def _archive_raw(trade_date: date, cash: bytes, delivery: bytes | None, fo: bytes | None) -> None:
    """Raw files archived before parsing so reprocessing is possible without
    re-hitting NSE (spec 01 requirement)."""
    day_dir = RAW_DIR / trade_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "cash_udiff.zip").write_bytes(cash)
    if delivery:
        (day_dir / "sec_bhavdata_full.csv").write_bytes(delivery)
    if fo:
        (day_dir / "fo_udiff.zip").write_bytes(fo)


def backfill(start: date, end: date, db_path: str | None = None) -> list[IngestResult]:
    """Calls ingest_date for each day in [start, end], skipping weekends
    up front (a cheap pre-filter; ingest_date still handles holidays via the
    404-as-non-trading-day path)."""
    results = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            results.append(ingest_date(d, db_path))
        d += timedelta(days=1)
    return results
