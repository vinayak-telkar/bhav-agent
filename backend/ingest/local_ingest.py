"""
Local-file ingest — the practical workaround for NSE's Akamai bot-detection
blocking scripted downloads (see ingest/bhavcopy.py's module docstring and
specs/01_ingest_bhavcopy.md's Changelog: confirmed blocked even from a normal
home-network IP, not just a cloud sandbox — Akamai's checks aren't
satisfiable by a bare HTTP client regardless of whose network it's on).

The user downloads the three files themselves via their own real browser
(which passes Akamai fine, since it executes JS and carries a real session)
and this module locates them in a downloads folder, then reuses
ingest/bhavcopy.py's exact parse+load logic (`_process_and_load`) to load
them into SQLite — there is no separate/duplicated parsing path.

Filename matching is deliberately forgiving, not exact: NSE's live filenames
were never confirmed in this build environment (network access blocked), and
browsers commonly append " (1)"-style suffixes to duplicate downloads.
Anything not confidently matched is surfaced as a candidate list so the user
confirms which file is which, rather than the app guessing silently.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ingest import bhavcopy
from ingest.bhavcopy import IngestResult
from app.data import db as db_module

DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"

FILE_TYPES = ("cash", "delivery", "fo")

FILE_LABELS = {
    "cash": "Cash market bhavcopy (OHLC, volume, VWAP)",
    "delivery": "Security-wise delivery data",
    "fo": "F&O bhavcopy (futures + options)",
}

_CANDIDATE_LOOKBACK_HOURS = 48
_MATCHABLE_EXTENSIONS = (".csv", ".zip")


def nse_download_links(trade_date: date) -> list[dict]:
    """Links for the user to open in their own browser. `direct_url` (the
    same pattern ingest/bhavcopy.py's automated path assumes) is confirmed
    correct against live NSE downloads as of 2026-08-03 — see specs/01's
    Changelog. A 404 on a specific date most often means that date's file
    isn't published yet (NSE typically publishes the cash bhavcopy by
    ~18:30 IST, F&O sometimes later — see IngestScreen.tsx's date-default
    logic), not that the URL pattern is wrong; `hub_url` (NSE's general
    reports landing page) is the fallback if a direct link ever does break.
    """
    yyyymmdd = trade_date.strftime("%Y%m%d")
    ddmmyyyy = trade_date.strftime("%d%m%Y")
    hub_url = "https://www.nseindia.com/all-reports"
    return [
        {
            "file_type": "cash",
            "label": FILE_LABELS["cash"],
            "direct_url": bhavcopy.CASH_UDIFF_URL.format(yyyymmdd=yyyymmdd),
            "hub_url": hub_url,
            "expected_filename_hint": f"BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv(.zip)",
        },
        {
            "file_type": "delivery",
            "label": FILE_LABELS["delivery"],
            "direct_url": bhavcopy.DELIVERY_URL.format(ddmmyyyy=ddmmyyyy),
            "hub_url": hub_url,
            "expected_filename_hint": f"sec_bhavdata_full_{ddmmyyyy}.csv",
        },
        {
            "file_type": "fo",
            "label": FILE_LABELS["fo"],
            "direct_url": bhavcopy.FO_UDIFF_URL.format(yyyymmdd=yyyymmdd),
            "hub_url": hub_url,
            "expected_filename_hint": f"BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv(.zip)",
        },
    ]


@dataclass
class FileMatch:
    file_type: str
    matched_path: Path | None
    candidates: list[Path] = field(default_factory=list)


def _expected_stem(file_type: str, trade_date: date) -> str:
    yyyymmdd = trade_date.strftime("%Y%m%d")
    ddmmyyyy = trade_date.strftime("%d%m%Y")
    return {
        "cash": f"bhavcopy_nse_cm_0_0_0_{yyyymmdd}_f_0000",
        "delivery": f"sec_bhavdata_full_{ddmmyyyy}",
        "fo": f"bhavcopy_nse_fo_0_0_0_{yyyymmdd}_f_0000",
    }[file_type]


def _strip_known_extensions(name: str) -> str:
    """Handles double extensions like '....csv.zip' — Path.stem only strips
    the last one."""
    p = Path(name)
    while p.suffix.lower() in _MATCHABLE_EXTENSIONS:
        p = p.with_suffix("")
    return p.name


def find_local_files(downloads_dir: Path | str, trade_date: date) -> dict[str, FileMatch]:
    downloads_dir = Path(downloads_dir)
    all_files = (
        [p for p in downloads_dir.iterdir() if p.is_file() and p.suffix.lower() in _MATCHABLE_EXTENSIONS]
        if downloads_dir.exists()
        else []
    )

    results: dict[str, FileMatch] = {}
    for file_type in FILE_TYPES:
        expected = _expected_stem(file_type, trade_date)
        matched = next(
            (p for p in all_files if _strip_known_extensions(p.name).lower().startswith(expected)), None
        )
        results[file_type] = FileMatch(file_type=file_type, matched_path=matched)

    matched_paths = {fm.matched_path for fm in results.values() if fm.matched_path is not None}
    cutoff = datetime.now().timestamp() - _CANDIDATE_LOOKBACK_HOURS * 3600
    unmatched_recent = sorted(
        (p for p in all_files if p not in matched_paths and p.stat().st_mtime >= cutoff),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for file_type in FILE_TYPES:
        results[file_type].candidates = unmatched_recent

    return results


def ingest_from_local_files(
    trade_date: date,
    cash_file: str | Path,
    delivery_file: str | Path | None = None,
    fo_file: str | Path | None = None,
    db_path: str | None = None,
) -> IngestResult:
    """Same parse+load logic as bhavcopy.ingest_date(), sourcing bytes from
    already-downloaded local files instead of a network request."""
    conn = db_module.get_connection(db_path)
    try:
        cash_path = Path(cash_file)
        if not cash_path.exists():
            return IngestResult(trade_date, "failed", error=f"cash bhavcopy file not found: {cash_path}")

        delivery_path = Path(delivery_file) if delivery_file else None
        fo_path = Path(fo_file) if fo_file else None

        return bhavcopy._process_and_load(
            conn,
            trade_date,
            cash_path.read_bytes(),
            delivery_path.read_bytes() if delivery_path and delivery_path.exists() else None,
            fo_path.read_bytes() if fo_path and fo_path.exists() else None,
        )
    except Exception as exc:  # noqa: BLE001 — ingest must report, not crash the run
        return IngestResult(trade_date, "failed", error=str(exc))
    finally:
        conn.close()
