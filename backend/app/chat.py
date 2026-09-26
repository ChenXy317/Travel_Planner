import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import cents_to_yuan
from app.config import Settings, get_settings
from app.db import get_engine, now_iso
from app.llm_runtime import get_llm_config
from app.models import Itinerary, Message

log = logging.getLogger("app.chat")


def log_startup(settings: Settings) -> None:
    cfg = get_llm_config()
    log.info(
        "database=%s plans=%s llm_host=%s search_key_set=%s",
        settings.db_path,
        settings.plans_dir,
        urlparse(cfg.base_url).hostname or "",
        str(bool(settings.search_api_key)).lower(),
    )


def document_file(itinerary_id: int) -> Path:
    return get_settings().plans_dir / f"{itinerary_id}.md"


def has_document(row: Itinerary) -> bool:
    return bool(row.document_path) and document_file(row.id).is_file()


def list_messages(limit: int) -> list[dict]:
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(Message).order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        ).all()
    rows.reverse()
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "tool_name": row.tool_name,
            "tool_call_id": row.tool_call_id,
            "tool_calls": json.loads(row.tool_calls_json) if row.tool_calls_json else None,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def list_itineraries(limit: int) -> list[dict]:
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(Itinerary).order_by(Itinerary.created_at.desc(), Itinerary.id.desc()).limit(limit)
        ).all()
        return [_summary(row) for row in rows]


def get_itinerary(itinerary_id: int) -> dict | None:
    with Session(get_engine()) as session:
        row = session.get(Itinerary, itinerary_id)
        if row is None:
            return None
        return {
            "itinerary_id": row.id,
            "title": row.title,
            "cities": json.loads(row.cities_json),
            "people": row.people,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "days": json.loads(row.days_json),
            "total_known_yuan": cents_to_yuan(row.total_known_cents),
            "budget_items": json.loads(row.budget_items_json),
            "unknown_items": json.loads(row.unknown_json),
            "sources": json.loads(row.sources_json),
            "has_document": has_document(row),
        }


def append_message(
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_calls: list | None = None,
) -> None:
    if role == "tool":
        content = content[:8000]
    with Session(get_engine()) as session:
        session.add(
            Message(
                role=role,
                content=content,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_calls_json=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                created_at=now_iso(),
            )
        )
        session.commit()


def load_model_messages(limit: int = 24):
    """把最近消息还原成模型输入。工具正文截到 1500 码，不成对的工具调用尾巴丢掉。"""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    with Session(get_engine()) as session:
        rows = session.scalars(
            select(Message).order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        ).all()
    rows.reverse()
    messages = []
    for row in _drop_unpaired(list(rows)):
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            calls = json.loads(row.tool_calls_json) if row.tool_calls_json else None
            if calls:
                messages.append(AIMessage(content=row.content, tool_calls=calls))
            else:
                messages.append(AIMessage(content=row.content))
        elif row.role == "tool":
            messages.append(
                ToolMessage(
                    content=row.content[:1500],
                    tool_call_id=row.tool_call_id or "",
                    name=row.tool_name or "",
                )
            )
    return messages


def _drop_unpaired(rows: list[Message]) -> list[Message]:
    kept: list[Message] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        calls = json.loads(row.tool_calls_json) if row.role == "assistant" and row.tool_calls_json else None
        if calls:
            ids = [call["id"] for call in calls]
            found = {}
            cursor = index + 1
            while cursor < len(rows) and rows[cursor].role == "tool":
                found[rows[cursor].tool_call_id] = rows[cursor]
                cursor += 1
            if all(tool_id in found for tool_id in ids):
                kept.append(row)
                for tool_id in ids:
                    kept.append(found[tool_id])
                index = cursor
                continue
            break
        if row.role == "tool":
            index += 1
            continue
        kept.append(row)
        index += 1
    return kept


def _summary(row: Itinerary) -> dict:
    return {
        "itinerary_id": row.id,
        "title": row.title,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "total_known_yuan": cents_to_yuan(row.total_known_cents),
        "has_document": has_document(row),
    }
