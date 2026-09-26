"""地理编码、预报、Overpass、OSRM 和 Brave 的 httpx 客户端。主机写死。"""

import hashlib
import html
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import format_km, weather_phrase
from app.config import get_settings
from app.db import get_engine
from app.models import GeoCache, RouteCache, SearchCache, WeatherCache

log = logging.getLogger("app.clients")

USER_AGENT = "TravelPlannerDemo/0.1 (single-user local demo)"
LLM_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
LLM_CONTEXT_CAPS = {
    "count": 5,
    "maximum_number_of_urls": 5,
    "maximum_number_of_tokens": 2048,
    "maximum_number_of_snippets_per_url": 3,
    "context_threshold_mode": "balanced",
}
PLACE_TAGS = {
    "博物馆": ("tourism", "museum"),
    "公园": ("leisure", "park"),
    "海滩": ("natural", "beach"),
    "景点": ("tourism", "attraction"),
    "观景点": ("tourism", "viewpoint"),
}
TTL = {
    "search": 24 * 3600,
    "geo": 30 * 24 * 3600,
    "weather": 6 * 3600,
    "route": 7 * 24 * 3600,
}
_TAG = re.compile(r"<[^>]+>")
_transport: httpx.BaseTransport | None = None


class _Now:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 26, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current


now_clock = _Now()
_now = lambda: datetime.now(timezone.utc)  # noqa: E731


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


def set_now(fn) -> None:
    global _now
    _now = fn


def use_test_clock() -> None:
    now_clock.current = datetime(2026, 9, 26, tzinfo=timezone.utc)
    set_now(now_clock)


class Gate:
    """外部请求不重叠，且两次开始至少相隔 1 秒。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0
        self._clock = time.monotonic
        self._sleep = time.sleep
        self.waits: list[float] = []
        self.depth = 0
        self.max_depth = 0

    def configure(self, clock, sleep) -> None:
        with self._lock:
            self._clock = clock
            self._sleep = sleep
            self._next = 0.0
            self.waits = []
            self.depth = 0
            self.max_depth = 0

    def call(self, fn):
        with self._lock:
            wait = self._next - self._clock()
            if wait < 0:
                wait = 0.0
            self.waits.append(wait)
            if wait:
                self._sleep(wait)
            self._next = self._clock() + 1.0
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)
            try:
                return fn()
            finally:
                self.depth -= 1


gate = Gate()


def cache_key(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _open_client(timeout: float) -> httpx.Client:
    kwargs = {"timeout": timeout, "headers": {"User-Agent": USER_AGENT}}
    if _transport is not None:
        kwargs["transport"] = _transport
    return httpx.Client(**kwargs)


def _log_call(provider: str, host: str, cache: str, status, elapsed_ms: int, query: str) -> None:
    log.info(
        "provider=%s host=%s cache=%s status=%s elapsed_ms=%s q=%s",
        provider,
        host,
        cache,
        status,
        elapsed_ms,
        (query or "")[:80],
    )


def send(method: str, url: str, timeout: float, provider: str, query: str, **kwargs) -> httpx.Response:
    host = urlparse(url).hostname or ""
    started = time.perf_counter()

    def once():
        with _open_client(timeout) as client:
            return client.request(method, url, **kwargs)

    try:
        response = gate.call(once)
    except httpx.TimeoutException:
        elapsed = int((time.perf_counter() - started) * 1000)
        _log_call(provider, host, "miss", "TimeoutException", elapsed, query)
        raise
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        _log_call(provider, host, "miss", type(exc).__name__, elapsed, query)
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    _log_call(provider, host, "miss", response.status_code, elapsed, query)
    return response


def read_cache(model, key: str, ttl: int) -> dict | None:
    with Session(get_engine()) as session:
        row = session.scalar(select(model).where(model.cache_key == key))
        if row is None:
            return None
        fetched = datetime.fromisoformat(row.fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if _now() - fetched > timedelta(seconds=ttl):
            session.delete(row)
            session.commit()
            return None
        return json.loads(row.payload_json)


def write_cache(model, key: str, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    with Session(get_engine()) as session:
        row = session.scalar(select(model).where(model.cache_key == key))
        if row is None:
            session.add(
                model(cache_key=key, payload_json=text, fetched_at=_now().isoformat())
            )
        else:
            row.payload_json = text
            row.fetched_at = _now().isoformat()
        session.commit()


def clean_snippet(text: str) -> str:
    cleaned = html.unescape(_TAG.sub("", text or "")).strip()
    return cleaned[:400]


def http_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _title_or_host(title, url: str) -> str:
    if isinstance(title, str) and title.strip():
        return title.strip()
    return urlparse(url).hostname or url


def normalize_llm_context(payload: dict) -> list[dict]:
    generic = []
    if isinstance(payload, dict):
        generic = ((payload.get("grounding") or {}).get("generic") or [])
    return _collect(generic)


def normalize_web_search(payload: dict) -> list[dict]:
    rows = []
    if isinstance(payload, dict):
        rows = ((payload.get("web") or {}).get("results") or [])
    results = []
    for item in rows:
        if not isinstance(item, dict) or not http_url(item.get("url")):
            continue
        url = item["url"]
        snippet = clean_snippet(str(item.get("description") or ""))
        if not snippet:
            continue
        results.append({"title": _title_or_host(item.get("title"), url), "url": url, "snippets": [snippet]})
        if len(results) >= 5:
            break
    return results


def _collect(rows) -> list[dict]:
    results = []
    for item in rows:
        if not isinstance(item, dict) or not http_url(item.get("url")):
            continue
        url = item["url"]
        raw = item.get("snippets") or []
        if isinstance(raw, str):
            raw = [raw]
        snippets = []
        for part in raw:
            if len(snippets) >= 3:
                break
            cleaned = clean_snippet(str(part))
            if cleaned:
                snippets.append(cleaned)
        if not snippets:
            continue
        results.append({"title": _title_or_host(item.get("title"), url), "url": url, "snippets": snippets})
        if len(results) >= 5:
            break
    return results


def _json_body(response: httpx.Response):
    try:
        return response.json()
    except Exception:
        return None


def geocode_place(name: str) -> dict:
    key = cache_key({"op": "geocode", "name": name})
    hit = read_cache(GeoCache, key, TTL["geo"])
    if hit is not None:
        _log_call("geocode", "geocoding-api.open-meteo.com", "hit", "-", 0, name)
        return hit
    try:
        response = send(
            "GET",
            "https://geocoding-api.open-meteo.com/v1/search",
            8,
            "geocode",
            name,
            params={"name": name, "count": 3, "language": "zh", "format": "json"},
        )
    except httpx.TimeoutException:
        return {"ok": False, "error": "地理编码服务失败"}
    except httpx.HTTPError:
        return {"ok": False, "error": "地理编码服务失败"}
    if response.status_code != 200:
        return {"ok": False, "error": "地理编码服务失败"}
    body = _json_body(response) or {}
    candidates = []
    for item in (body.get("results") or [])[:3]:
        if item.get("latitude") is None or item.get("longitude") is None:
            continue
        label = item.get("name") or name
        if item.get("admin1"):
            label = f"{label}, {item['admin1']}"
        candidates.append(
            {
                "name": label,
                "country": item.get("country") or "",
                "country_code": item.get("country_code") or "",
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "timezone": item.get("timezone") or "",
            }
        )
    if not candidates:
        return {"ok": False, "error": "找不到该地点"}
    payload = {"ok": True, "results": candidates}
    if len(candidates) > 1:
        payload["needs_user_choice"] = True
    write_cache(GeoCache, key, payload)
    return payload


def fetch_forecast(latitude, longitude, start_date: str, end_date: str) -> dict:
    key = cache_key(
        {"op": "forecast", "lat": str(latitude), "lon": str(longitude), "start": start_date, "end": end_date}
    )
    hit = read_cache(WeatherCache, key, TTL["weather"])
    if hit is not None:
        _log_call("forecast", "api.open-meteo.com", "hit", "-", 0, f"{latitude},{longitude}")
        return hit
    query = f"{latitude},{longitude} {start_date}"
    try:
        response = send(
            "GET",
            "https://api.open-meteo.com/v1/forecast",
            8,
            "forecast",
            query,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "auto",
                "start_date": start_date,
                "end_date": end_date,
            },
        )
    except httpx.TimeoutException:
        return {"ok": False, "error": "天气服务失败"}
    except httpx.HTTPError:
        return {"ok": False, "error": "天气服务失败"}
    if response.status_code != 200:
        return {"ok": False, "error": "天气服务失败"}
    body = _json_body(response) or {}
    daily = body.get("daily") or {}
    times = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_sum") or []
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    expected = (end - start).days + 1
    if len(times) < expected or len(codes) < expected or len(highs) < expected or len(lows) < expected:
        return {"ok": False, "error": "预报超出范围"}
    days = []
    for index in range(expected):
        if times[index] != (start + timedelta(days=index)).isoformat():
            return {"ok": False, "error": "预报超出范围"}
        code = int(codes[index])
        days.append(
            {
                "date": times[index],
                "t_min": lows[index],
                "t_max": highs[index],
                "precip_mm": rains[index] if index < len(rains) else None,
                "weather_code": code,
                "weather_line": weather_phrase(code, lows[index], highs[index]),
            }
        )
    payload = {"ok": True, "days": days}
    write_cache(WeatherCache, key, payload)
    return payload


def fetch_places(latitude, longitude, category: str, limit: int) -> dict:
    tag = PLACE_TAGS.get(category)
    if tag is None:
        return {"ok": False, "error": "类别不支持"}
    key = cache_key(
        {"op": "places", "lat": str(latitude), "lon": str(longitude), "category": category, "limit": limit}
    )
    hit = read_cache(GeoCache, key, TTL["geo"])
    if hit is not None:
        _log_call("overpass", "overpass-api.de", "hit", "-", 0, category)
        return hit
    osm_key, osm_value = tag
    query = (
        f"[out:json][timeout:12];"
        f"(node[\"{osm_key}\"=\"{osm_value}\"](around:8000,{latitude},{longitude});"
        f"way[\"{osm_key}\"=\"{osm_value}\"](around:8000,{latitude},{longitude}););"
        f"out tags center {limit};"
    )
    try:
        response = send(
            "POST",
            "https://overpass-api.de/api/interpreter",
            20,
            "overpass",
            category,
            content=query,
            headers={"Content-Type": "text/plain"},
        )
    except httpx.TimeoutException:
        return {"ok": False, "error": "地点服务失败"}
    except httpx.HTTPError:
        return {"ok": False, "error": "地点服务失败"}
    if response.status_code == 429 or response.status_code != 200:
        return {"ok": False, "error": "地点服务失败"}
    body = _json_body(response) or {}
    places = []
    for element in body.get("elements") or []:
        tags = element.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        if element.get("type") == "way":
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        else:
            lat, lon = element.get("lat"), element.get("lon")
        if lat is None or lon is None:
            continue
        places.append({"name": name, "latitude": lat, "longitude": lon})
        if len(places) >= limit:
            break
    payload = {"ok": True, "results": places}
    write_cache(GeoCache, key, payload)
    return payload


def fetch_route(lat1, lon1, lat2, lon2, mode: str) -> dict:
    if mode not in {"foot", "driving"}:
        return {"ok": False, "error": "出行方式不支持"}
    key = cache_key(
        {"op": "route", "lat1": str(lat1), "lon1": str(lon1), "lat2": str(lat2), "lon2": str(lon2), "mode": mode}
    )
    hit = read_cache(RouteCache, key, TTL["route"])
    if hit is not None:
        _log_call("osrm", "router.project-osrm.org", "hit", "-", 0, mode)
        return hit
    url = f"https://router.project-osrm.org/route/v1/{mode}/{lon1},{lat1};{lon2},{lat2}"
    try:
        response = send("GET", url, 10, "osrm", mode, params={"overview": "false"})
    except httpx.TimeoutException:
        return {"ok": False, "error": "路程服务失败"}
    except httpx.HTTPError:
        return {"ok": False, "error": "路程服务失败"}
    if response.status_code != 200:
        return {"ok": False, "error": "路程服务失败"}
    body = _json_body(response) or {}
    routes = body.get("routes") or []
    if body.get("code") != "Ok" or not routes:
        return {"ok": False, "error": "路程服务失败"}
    duration = routes[0].get("duration")
    distance = routes[0].get("distance")
    if duration is None or distance is None:
        return {"ok": False, "error": "路程服务失败"}
    if duration == 0:
        minutes = 0
    else:
        minutes = max(1, round(duration / 60))
    kilometers = float(format_km(distance / 1000))
    payload = {"ok": True, "minutes": minutes, "kilometers": kilometers, "mode": mode}
    write_cache(RouteCache, key, payload)
    return payload


def _plan_denied(status: int, text: str) -> bool:
    if status == 404:
        return True
    if status not in {200, 402, 403}:
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("rate limit", "too many", "过于频繁")):
        return False
    return any(token in lowered for token in ("plan", "subscription", "not subscribed", "llm context"))


def _flag_key() -> str:
    return cache_key({"op": "brave-no-llm-context"})


def _search_headers() -> dict:
    return {
        "X-Subscription-Token": get_settings().search_api_key.strip(),
        "Accept": "application/json",
    }


def brave_search(query: str, country: str, search_lang: str) -> dict:
    if not get_settings().search_api_key.strip():
        return {"ok": False, "error": "未配置 SEARCH_API_KEY"}
    key = cache_key({"query": query, "country": country, "search_lang": search_lang, "caps": LLM_CONTEXT_CAPS})
    hit = read_cache(SearchCache, key, TTL["search"])
    if hit is not None:
        _log_call("brave", "api.search.brave.com", "hit", "-", 0, query)
        return {"ok": True, "results": hit["results"]}
    skip_primary = read_cache(SearchCache, _flag_key(), TTL["search"]) is not None
    try:
        if not skip_primary:
            response = send(
                "GET",
                LLM_CONTEXT_URL,
                15,
                "brave",
                query,
                params={"q": query, "country": country, "search_lang": search_lang, **LLM_CONTEXT_CAPS},
                headers=_search_headers(),
            )
            if response.status_code == 401:
                return {"ok": False, "error": "搜索未授权"}
            if response.status_code == 429:
                return {"ok": False, "error": "搜索请求过于频繁"}
            if _plan_denied(response.status_code, response.text):
                write_cache(SearchCache, _flag_key(), {"endpoint": "web_search"})
            elif response.status_code != 200:
                return {"ok": False, "error": "搜索服务失败"}
            else:
                results = normalize_llm_context(_json_body(response) or {})
                if not results:
                    return {"ok": False, "error": "搜索无结果"}
                write_cache(SearchCache, key, {"endpoint": "llm_context", "results": results})
                return {"ok": True, "results": results}
        response = send(
            "GET",
            WEB_SEARCH_URL,
            15,
            "brave",
            query,
            params={"q": query, "country": country, "search_lang": search_lang, "count": 5},
            headers=_search_headers(),
        )
    except httpx.TimeoutException:
        return {"ok": False, "error": "搜索超时"}
    except httpx.HTTPError:
        return {"ok": False, "error": "搜索服务失败"}
    if response.status_code == 401:
        return {"ok": False, "error": "搜索未授权"}
    if response.status_code == 429:
        return {"ok": False, "error": "搜索请求过于频繁"}
    if response.status_code != 200:
        return {"ok": False, "error": "搜索服务失败"}
    results = normalize_web_search(_json_body(response) or {})
    if not results:
        return {"ok": False, "error": "搜索无结果"}
    write_cache(SearchCache, key, {"endpoint": "web_search", "results": results})
    return {"ok": True, "results": results}
