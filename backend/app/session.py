"""单次请求的会话事实。搜索次数只留在内存里。"""

import json
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.checks import point_key
from app.db import get_engine, now_iso
from app.models import SessionState

effect_lock = threading.RLock()
_requests: dict[str, "RequestState"] = {}


@dataclass
class AgentContext:
    request_id: str


@dataclass
class RequestState:
    search_count: int = 0
    seen_urls: set[str] = field(default_factory=set)
    url_titles: dict[str, str] = field(default_factory=dict)
    seen_order: list[str] = field(default_factory=list)
    points: dict[tuple[str, str], str] = field(default_factory=dict)
    routes: dict[tuple[str, str, str, str, str], tuple[int, float]] = field(default_factory=dict)
    weather_lines: dict[str, str] = field(default_factory=dict)
    last_budget_cents: int | None = None
    last_budget: dict | None = None


def load_state() -> RequestState:
    state = RequestState()
    with Session(get_engine()) as session:
        row = session.get(SessionState, 1)
        if row is None:
            return state
        state.last_budget_cents = row.last_budget_cents
        if row.last_budget_json:
            state.last_budget = json.loads(row.last_budget_json)
        for item in json.loads(row.seen_urls_json or "[]"):
            url = item.get("url")
            if not url:
                continue
            state.seen_order.append(url)
            state.seen_urls.add(url)
            state.url_titles[url] = item.get("title") or ""
        for key, name in json.loads(row.points_json or "{}").items():
            lat, lon = key.split(",", 1)
            state.points[(lat, lon)] = name
        for key, value in json.loads(row.routes_json or "{}").items():
            left, right, mode = key.split("|")
            lat1, lon1 = left.split(",", 1)
            lat2, lon2 = right.split(",", 1)
            state.routes[(lat1, lon1, lat2, lon2, mode)] = (value["minutes"], value["kilometers"])
        state.weather_lines = json.loads(row.weather_json or "{}")
    return state


def save_state(state: RequestState) -> None:
    seen = [
        {"url": url, "title": state.url_titles.get(url) or _hostname(url), "seen_at": now_iso()}
        for url in state.seen_order
    ]
    points = {f"{lat},{lon}": name for (lat, lon), name in state.points.items()}
    routes = {
        f"{lat1},{lon1}|{lat2},{lon2}|{mode}": {"minutes": minutes, "kilometers": kilometers}
        for (lat1, lon1, lat2, lon2, mode), (minutes, kilometers) in state.routes.items()
    }
    with Session(get_engine()) as session:
        row = session.get(SessionState, 1)
        if row is None:
            row = SessionState(
                id=1,
                seen_urls_json="[]",
                points_json="{}",
                routes_json="{}",
                weather_json="{}",
                updated_at=now_iso(),
            )
            session.add(row)
        row.last_budget_cents = state.last_budget_cents
        row.last_budget_json = json.dumps(state.last_budget, ensure_ascii=False) if state.last_budget else None
        row.seen_urls_json = json.dumps(seen, ensure_ascii=False)
        row.points_json = json.dumps(points, ensure_ascii=False)
        row.routes_json = json.dumps(routes, ensure_ascii=False)
        row.weather_json = json.dumps(state.weather_lines, ensure_ascii=False)
        row.updated_at = now_iso()
        session.commit()


def remember_urls(state: RequestState, pairs: list[tuple[str, str]]) -> None:
    for url, title in pairs:
        if url in state.seen_order:
            state.seen_order.remove(url)
        state.seen_order.append(url)
        state.seen_urls.add(url)
        state.url_titles[url] = title or _hostname(url)
    while len(state.seen_order) > 200:
        old = state.seen_order.pop(0)
        state.seen_urls.discard(old)
        state.url_titles.pop(old, None)


def remember_point(state: RequestState, latitude, longitude, name: str) -> None:
    state.points[point_key(latitude, longitude)] = name


def begin_request(request_id: str) -> RequestState:
    state = load_state()
    with effect_lock:
        _requests[request_id] = state
    return state


def end_request(request_id: str) -> None:
    with effect_lock:
        _requests.pop(request_id, None)


def get_state(request_id: str) -> RequestState:
    with effect_lock:
        return _requests[request_id]


def _hostname(url: str) -> str:
    return urlparse(url).hostname or url
