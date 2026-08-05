"""
FastAPI app entrypoint. Registers routers from app/routes/ and wires the
daily digest job via APScheduler. See TECH_SPEC.md §7.
"""
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from agent.digest_graph import run_daily_digest  # noqa: E402
from agent.resolve_insights import resolve_insights  # noqa: E402
from app.config import DEMO_USER_ID  # noqa: E402
from app.data import db as db_module  # noqa: E402
from app.routes import chat, digest, ingest, symbols, watchlist  # noqa: E402

scheduler = AsyncIOScheduler()


async def _scheduled_digest_run(user_id: str) -> None:
    """Resolve prior insights against fresh price data, then generate new
    ones — same order as the manual POST /digest/run trigger (spec 11)."""
    resolve_insights(user_id)
    await run_daily_digest(user_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_module.init_db()
    # Real usage starts with an empty DB — no seeded synthetic data (see
    # ingest/seed_data.py's docstring for why). This just bootstraps the demo
    # user row so watchlist FK constraints are satisfiable before any real
    # ingest or Manage-screen action has happened.
    db_module.ensure_demo_user(DEMO_USER_ID)
    # Runs once daily after NSE's EOD files are typically available. Demo/testing
    # can bypass the wait via POST /digest/run (app/routes/digest.py).
    scheduler.add_job(_scheduled_digest_run, "cron", hour=18, minute=30, args=[DEMO_USER_ID], id="daily_digest")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Bhavcopy Flow Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server default port
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(symbols.router)
app.include_router(watchlist.router)
app.include_router(digest.router)
app.include_router(ingest.router)
app.include_router(chat.router)
