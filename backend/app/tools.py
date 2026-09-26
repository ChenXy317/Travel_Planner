"""九个行程工具。彼此不调用，失败时返回 JSON 而不是抛给图。"""

import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from app import clients
from sqlalchemy.orm import Session

from app.checks import (
    apply_budget,
    consume_search_slot,
    normalize_cities,
    point_key,
    route_key,
    shanghai_today,
    validate_itinerary,
)
from app.chat import list_itineraries as list_saved
from app.db import get_engine, now_iso
from app.documents import write_plan
from app.models import Itinerary
from app.session import effect_lock, get_state, remember_point, remember_urls, save_state

log = logging.getLogger("app.tools")

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_LANG = re.compile(r"^[a-z]{2}(-[a-z]{2,8})?$")


def _dump(payload: dict) -> str:
    if not payload.get("ok"):
        log.warning("工具失败 %s", payload.get("error"))
    return json.dumps(payload, ensure_ascii=False)


def _run(body) -> str:
    try:
        with effect_lock:
            payload = body()
        return _dump(payload)
    except Exception:
        log.exception("工具内部错误")
        return json.dumps({"ok": False, "error": "内部错误"}, ensure_ascii=False)


def _fail(error: str) -> dict:
    return {"ok": False, "error": error}


def _known_point(state, latitude, longitude) -> bool:
    return point_key(latitude, longitude) in state.points


class BudgetItemIn(BaseModel):
    name: str
    price_yuan: float | int | str | None = None
    quantity: int
    source_url: str | None = None


class StopIn(BaseModel):
    period: str
    name: str
    category: str


class LegIn(BaseModel):
    from_name: str
    to_name: str
    from_latitude: float
    from_longitude: float
    to_latitude: float
    to_longitude: float
    mode: str
    minutes: int
    kilometers: float


class DayIn(BaseModel):
    date: str
    weather_line: str
    stops: list[StopIn]
    legs: list[LegIn] = Field(default_factory=list)


class SourceIn(BaseModel):
    url: str
    title: str | None = None


@tool
def geocode(name: str, *, runtime: ToolRuntime) -> str:
    """把地点名称解析为最多 3 个坐标。多于一个候选时停下来问用户，不要自己挑。"""

    def body():
        cleaned = (name or "").strip()
        if not 1 <= len(cleaned) <= 200:
            return _fail("地点名称长度不合法")
        result = clients.geocode_place(cleaned)
        if not result.get("ok"):
            return result
        state = get_state(runtime.context.request_id)
        for item in result["results"]:
            remember_point(state, item["latitude"], item["longitude"], item["name"])
        save_state(state)
        return result

    return _run(body)


@tool
def get_weather(latitude: float, longitude: float, start_date: str, days: int, *, runtime: ToolRuntime) -> str:
    """查询已解析坐标的每日天气。days 只能是 1 到 5。"""

    def body():
        state = get_state(runtime.context.request_id)
        if not _known_point(state, latitude, longitude):
            return _fail("坐标不是来自 geocode 或 search_places")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 5:
            return _fail("天数需在 1 到 5")
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            return _fail("预报超出范围")
        end = start + timedelta(days=days - 1)
        today = shanghai_today()
        if start < today or end > today + timedelta(days=15):
            return _fail("预报超出范围")
        result = clients.fetch_forecast(latitude, longitude, start.isoformat(), end.isoformat())
        if not result.get("ok"):
            return result
        for day in result["days"]:
            state.weather_lines[day["date"]] = day["weather_line"]
        save_state(state)
        return result

    return _run(body)


@tool
def search_places(
    latitude: float,
    longitude: float,
    category: str,
    limit: int = 5,
    *,
    runtime: ToolRuntime,
) -> str:
    """在坐标周围 8 公里内查找博物馆、公园、海滩、景点或观景点。只返回名称和坐标。"""

    def body():
        state = get_state(runtime.context.request_id)
        if category not in clients.PLACE_TAGS:
            return _fail("类别不支持")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 6:
            return _fail("条数需在 1 到 6")
        if not _known_point(state, latitude, longitude):
            return _fail("坐标不是来自 geocode 或 search_places")
        result = clients.fetch_places(latitude, longitude, category, limit)
        if not result.get("ok"):
            return result
        for item in result["results"]:
            remember_point(state, item["latitude"], item["longitude"], item["name"])
        save_state(state)
        return result

    return _run(body)


@tool
def web_search(query: str, country: str = "ALL", search_lang: str = "zh-hans", *, runtime: ToolRuntime) -> str:
    """搜索开放时间、门票和闭馆信息。只返回标题、链接和片段，一次回答最多 4 次。"""

    def body():
        cleaned = (query or "").strip()
        if not cleaned:
            return _fail("查询不能为空")
        if len(cleaned) > 600 or len(cleaned.split()) > 75:
            return _fail("查询过长")
        normalized_country = (country or "ALL").strip().upper()
        if normalized_country != "ALL" and not _COUNTRY.fullmatch(normalized_country):
            return _fail("国家代码不合法")
        normalized_lang = (search_lang or "zh-hans").strip()
        if not _LANG.fullmatch(normalized_lang):
            return _fail("语言代码不合法")
        state = get_state(runtime.context.request_id)
        error = consume_search_slot(state)
        if error:
            return _fail(error)
        result = clients.brave_search(cleaned, normalized_country, normalized_lang)
        if not result.get("ok"):
            return result
        remember_urls(state, [(item["url"], item["title"]) for item in result["results"]])
        save_state(state)
        return {"ok": True, "results": result["results"]}

    return _run(body)


@tool
def estimate_route(
    from_name: str,
    from_latitude: float,
    from_longitude: float,
    to_name: str,
    to_latitude: float,
    to_longitude: float,
    mode: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """估算两个已解析坐标之间的步行或驾车路程。mode 为 foot 或 driving。"""

    def body():
        state = get_state(runtime.context.request_id)
        if not _known_point(state, from_latitude, from_longitude) or not _known_point(
            state, to_latitude, to_longitude
        ):
            return _fail("坐标不是来自 geocode 或 search_places")
        if mode not in {"foot", "driving"}:
            return _fail("出行方式不支持")
        result = clients.fetch_route(from_latitude, from_longitude, to_latitude, to_longitude, mode)
        if not result.get("ok"):
            return result
        state.routes[route_key(from_latitude, from_longitude, to_latitude, to_longitude, mode)] = (
            result["minutes"],
            result["kilometers"],
        )
        save_state(state)
        return {
            "ok": True,
            "from": from_name,
            "to": to_name,
            "mode": mode,
            "minutes": result["minutes"],
            "kilometers": result["kilometers"],
        }

    return _run(body)


@tool
def estimate_budget(people: int, nights: int, items: list[BudgetItemIn], *, runtime: ToolRuntime) -> str:
    """按单价乘数量汇总已知费用。带链接的价格必须来自本会话搜索结果。"""

    def body():
        state = get_state(runtime.context.request_id)
        payload = apply_budget(state, people, nights, items)
        if payload.get("ok"):
            save_state(state)
        return payload

    return _run(body)


@tool
def save_itinerary(
    title: str,
    cities: list[str],
    people: int,
    days: list[DayIn],
    total_known_yuan: str,
    unknown_items: list[str],
    sources: list[SourceIn],
    *,
    runtime: ToolRuntime,
) -> str:
    """用户明确同意后保存行程。总额、天气句和路程必须与已记录的工具结果一致。"""

    def body():
        state = get_state(runtime.context.request_id)
        day_rows = [_plain(day) for day in days]
        source_rows = [_plain(source) for source in sources]
        payload = {
            "title": title,
            "cities": cities,
            "people": people,
            "days": day_rows,
            "total_known_yuan": total_known_yuan,
            "unknown_items": unknown_items,
            "sources": source_rows,
        }
        error = validate_itinerary(payload, state)
        if error:
            return _fail(error)
        city_names = normalize_cities(cities)
        stored_sources = _source_list(state, source_rows)
        with Session(get_engine()) as session:
            row = Itinerary(
                title=str(title).strip(),
                cities_json=json.dumps(city_names, ensure_ascii=False),
                people=people,
                start_date=day_rows[0]["date"],
                end_date=day_rows[-1]["date"],
                days_json=json.dumps(day_rows, ensure_ascii=False),
                total_known_cents=state.last_budget_cents,
                budget_items_json=json.dumps(state.last_budget["items"], ensure_ascii=False),
                unknown_json=json.dumps(state.last_budget["unknown"], ensure_ascii=False),
                sources_json=json.dumps(stored_sources, ensure_ascii=False),
                document_path=None,
                created_at=now_iso(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            itinerary_id = row.id
        return {"ok": True, "itinerary_id": itinerary_id}

    return _run(body)


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _source_list(state, source_rows: list[dict]) -> list[dict]:
    urls = []
    for source in source_rows:
        url = source.get("url")
        if url and url not in urls:
            urls.append(url)
    for item in (state.last_budget or {}).get("items") or []:
        url = item.get("source_url")
        if url and url not in urls:
            urls.append(url)
    stored = []
    for url in urls:
        stored.append({"title": state.url_titles.get(url) or urlparse(url).hostname or url, "url": url})
    return stored


@tool
def write_plan_document(itinerary_id: int, *, runtime: ToolRuntime) -> str:
    """按已保存的行程写出计划文档。不要提供正文。"""

    def body():
        return write_plan(itinerary_id)

    return _run(body)


@tool
def list_itineraries(*, runtime: ToolRuntime) -> str:
    """列出最近保存的行程。"""

    def body():
        return {"ok": True, "itineraries": list_saved(10)}

    return _run(body)


TOOLS = [
    geocode,
    get_weather,
    search_places,
    web_search,
    estimate_route,
    estimate_budget,
    save_itinerary,
    write_plan_document,
    list_itineraries,
]
