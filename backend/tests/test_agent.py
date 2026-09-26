import json

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import clients
from app.chat import load_model_messages
from app.checks import weather_phrase
from app.config import get_settings
from tests.conftest import load_fixture
from tests.fake_model import ScriptedChatModel


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        events.append((event, json.loads(data)))
    return events


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _install(monkeypatch, replies, handler):
    monkeypatch.setattr("app.agent.build_chat_model", lambda: ScriptedChatModel(replies=list(replies)))
    clients.set_transport(httpx.MockTransport(handler))


def _post(client, message: str) -> list[tuple[str, dict]]:
    with client.stream("POST", "/api/v1/chat", json={"message": message}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    return parse_sse(body)


def test_geocode_stream_replays_tool_pair(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "geocoding-api.open-meteo.com" in str(request.url)
        return httpx.Response(200, json=load_fixture("geocode_qingdao.json"))

    _install(
        monkeypatch,
        [
            _call("geocode", {"name": "青岛"}, "call_1"),
            AIMessage(content="已找到青岛。"),
        ],
        handler,
    )
    events = _post(client, "青岛")
    assert [kind for kind, _data in events] == ["tool_call", "tool_result", "token", "done"]
    messages = load_model_messages()
    assert isinstance(messages[0], HumanMessage)
    assert messages[1].tool_calls[0]["name"] == "geocode"
    assert isinstance(messages[2], ToolMessage)
    assert messages[2].tool_call_id == messages[1].tool_calls[0]["id"]
    assert isinstance(messages[3], AIMessage)
    assert messages[3].content == "已找到青岛。"


def test_eight_hop_writes_plan(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "geocoding-api.open-meteo.com" in url:
            return httpx.Response(200, json=load_fixture("geocode_qingdao.json"))
        if "api.open-meteo.com/v1/forecast" in url:
            return httpx.Response(200, json=load_fixture("forecast_qingdao.json"))
        if "overpass-api.de" in url:
            return httpx.Response(200, json=load_fixture("overpass_museum.json"))
        if "router.project-osrm.org" in url:
            return httpx.Response(200, json=load_fixture("osrm_driving.json"))
        if "llm/context" in url:
            return httpx.Response(200, json=load_fixture("brave_llm_context.json"))
        raise AssertionError(url)

    line = weather_phrase(0, 15.2, 22.4)
    replies = [
        _call("geocode", {"name": "青岛"}, "c1"),
        _call(
            "get_weather",
            {"latitude": 36.0671, "longitude": 120.3826, "start_date": "2026-09-26", "days": 1},
            "c2",
        ),
        _call(
            "search_places",
            {"latitude": 36.0671, "longitude": 120.3826, "category": "博物馆", "limit": 5},
            "c3",
        ),
        _call("web_search", {"query": "青岛市博物馆 开放时间"}, "c4"),
        _call(
            "estimate_route",
            {
                "from_name": "青岛海滨",
                "from_latitude": 36.0671,
                "from_longitude": 120.3826,
                "to_name": "青岛市博物馆",
                "to_latitude": 36.07,
                "to_longitude": 120.39,
                "mode": "driving",
            },
            "c5",
        ),
        _call(
            "estimate_budget",
            {
                "people": 2,
                "nights": 2,
                "items": [
                    {"name": "酒店", "price_yuan": "400", "quantity": 2},
                    {"name": "门票", "price_yuan": None, "quantity": 1},
                ],
            },
            "c6",
        ),
        _call(
            "save_itinerary",
            {
                "title": "青岛一日",
                "cities": ["青岛"],
                "people": 2,
                "days": [
                    {
                        "date": "2026-09-26",
                        "weather_line": line,
                        "stops": [
                            {"period": "上午", "name": "青岛海滨", "category": "景点"},
                            {"period": "下午", "name": "青岛市博物馆", "category": "博物馆"},
                        ],
                        "legs": [
                            {
                                "from_name": "青岛海滨",
                                "to_name": "青岛市博物馆",
                                "from_latitude": 36.0671,
                                "from_longitude": 120.3826,
                                "to_latitude": 36.07,
                                "to_longitude": 120.39,
                                "mode": "driving",
                                "minutes": 10,
                                "kilometers": 2.5,
                            }
                        ],
                    }
                ],
                "total_known_yuan": "800.00",
                "unknown_items": ["门票"],
                "sources": [{"url": "https://example.org/museum", "title": "忽略"}],
            },
            "c7",
        ),
        _call("write_plan_document", {"itinerary_id": 1}, "c8"),
        AIMessage(content="计划已写好。"),
    ]
    _install(monkeypatch, replies, handler)
    events = _post(client, "去青岛")
    names = [data["name"] for kind, data in events if kind == "tool_call"]
    assert names == [
        "geocode",
        "get_weather",
        "search_places",
        "web_search",
        "estimate_route",
        "estimate_budget",
        "save_itinerary",
        "write_plan_document",
    ], events
    text = (get_settings().plans_dir / "1.md").read_text(encoding="utf-8")
    assert "800.00" in text
    assert "https://example.org/museum" in text
    assert "青岛市博物馆 — https://example.org/museum" in text
    assert "忽略" not in text


def test_fifth_search_stops_before_the_network(client, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "llm/context" in str(request.url)
        calls["n"] += 1
        return httpx.Response(200, json=load_fixture("brave_llm_context.json"))

    replies = [_call("web_search", {"query": f"青岛博物馆 {index}"}, f"s{index}") for index in range(5)]
    replies.append(AIMessage(content="搜满了"))
    _install(monkeypatch, replies, handler)
    events = _post(client, "帮我搜")
    results = [data for kind, data in events if kind == "tool_result"]
    assert len(results) == 5
    assert results[4]["ok"] is False
    assert results[4]["content"]["error"] == "已达搜索次数"
    assert calls["n"] <= 4
