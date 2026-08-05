"""
POST /chat, GET /chat/stream — Mode 2 interrogation (specs/12). Read-only:
never touches save_insight/ingest_local_bhavcopy (agent/chat_graph.py's
READ_ONLY_TOOLS allowlist enforces this at the tool-binding level, not just
here).
"""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.chat_graph import answer, stream_answer
from app.config import DEMO_USER_ID

router = APIRouter()


class ChatRequest(BaseModel):
    question: str


@router.post("/chat")
async def chat(body: ChatRequest) -> dict:
    return {"answer": await answer(DEMO_USER_ID, body.question)}


@router.get("/chat/stream")
async def chat_stream(question: str) -> StreamingResponse:
    """Server-Sent Events: {"progress": "..."} lines while the agent
    reasons/calls tools, then a final {"answer": "..."} — same UX pattern as
    GET /digest/run-stream."""

    async def event_stream():
        try:
            async for kind, text in stream_answer(DEMO_USER_ID, question):
                yield f"data: {json.dumps({kind: text})}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface to the client, don't just drop the connection
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
