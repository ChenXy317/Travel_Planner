import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.agent import stream_chat
from app.chat import document_file, get_itinerary, list_itineraries, list_messages, log_startup
from app.config import get_settings
from app.db import get_engine, init_db
from app.llm_config import router as llm_router
from app.models import Itinerary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log_startup(get_settings())
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(llm_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _missing(error: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"ok": False, "error": error})


class ChatIn(BaseModel):
    message: str = ""


@app.post("/api/v1/chat")
async def chat(body: ChatIn):
    text = body.message.strip()
    if not text:
        return JSONResponse(status_code=400, content={"ok": False, "error": "消息不能为空"})
    if len(text) > 2000:
        return JSONResponse(status_code=400, content={"ok": False, "error": "消息过长"})
    return StreamingResponse(
        stream_chat(text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/v1/messages")
def messages(limit: int = Query(default=24, ge=1, le=24)) -> dict:
    return {"messages": list_messages(limit)}


@app.get("/api/v1/itineraries")
def itineraries(limit: int = Query(default=10, ge=1, le=10)) -> dict:
    return {"itineraries": list_itineraries(limit)}


@app.get("/api/v1/itineraries/{itinerary_id}")
def itinerary_detail(itinerary_id: int):
    row = get_itinerary(itinerary_id)
    if row is None:
        return _missing("行程不存在")
    return row


@app.get("/api/v1/itineraries/{itinerary_id}/document")
def itinerary_document(itinerary_id: int):
    from sqlalchemy.orm import Session

    with Session(get_engine()) as session:
        row = session.get(Itinerary, itinerary_id)
        if row is None:
            return _missing("行程不存在")
    path = document_file(itinerary_id).resolve()
    plans = get_settings().plans_dir.resolve()
    if path.parent != plans or not path.is_file():
        return _missing("行程不存在")
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{itinerary_id}.md",
    )
