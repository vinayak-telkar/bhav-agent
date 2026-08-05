"""
Manual-download ingest flow — see ingest/local_ingest.py's docstring for why
this exists (NSE's Akamai bot-detection blocks scripted downloads). The user
clicks through NSE's site in their own browser, then this flow locates the
downloaded files and loads them via the same parser the automated path uses.

Not in tech spec §7's original route list — added to support the manual
workaround. `POST /ingest/run-local` calls `ingest_from_local_files()`
directly rather than going through the MCP tool of the same underlying
function; both exist (see specs/06's note) — the MCP tool makes this action
available to an agent, this route serves the UI without LLM latency for a
deterministic action, exactly like `POST /digest/run` calls `run_daily_digest`
directly.
"""
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from ingest import local_ingest

router = APIRouter()


@router.get("/ingest/links")
def get_download_links(trade_date: str) -> list[dict]:
    return local_ingest.nse_download_links(date.fromisoformat(trade_date))


class CheckDownloadsRequest(BaseModel):
    trade_date: str
    downloads_dir: str | None = None


@router.post("/ingest/check")
def check_downloads(body: CheckDownloadsRequest) -> dict:
    downloads_dir = body.downloads_dir or local_ingest.DEFAULT_DOWNLOADS_DIR
    matches = local_ingest.find_local_files(downloads_dir, date.fromisoformat(body.trade_date))
    return {
        file_type: {
            "label": local_ingest.FILE_LABELS[file_type],
            "matched_path": str(match.matched_path) if match.matched_path else None,
            "candidates": [str(p) for p in match.candidates],
        }
        for file_type, match in matches.items()
    }


class RunLocalIngestRequest(BaseModel):
    trade_date: str
    cash_file: str
    delivery_file: str | None = None
    fo_file: str | None = None


@router.post("/ingest/run-local")
def run_local_ingest(body: RunLocalIngestRequest) -> dict:
    result = local_ingest.ingest_from_local_files(
        date.fromisoformat(body.trade_date), body.cash_file, body.delivery_file, body.fo_file
    )
    return {
        "trade_date": result.trade_date.isoformat(),
        "status": result.status,
        "symbols_loaded": result.symbols_loaded,
        "corporate_actions_flagged": result.corporate_actions_flagged,
        "error": result.error,
    }
