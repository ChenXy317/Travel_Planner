import json
from decimal import Decimal
from types import SimpleNamespace

from app.checks import (
    BudgetItem,
    apply_budget,
    consume_search_slot,
    estimate_budget_cents,
    route_key,
    validate_itinerary,
    weather_phrase,
)
from app.session import RequestState, begin_request, end_request, load_state, save_state
from app.tools import save_itinerary, web_search


def test_known_items_sum_to_cents_and_null_price_is_unknown():
    outcome = estimate_budget_cents(
        2,
        2,
        [
            BudgetItem("酒店", Decimal("400"), 2, None),
            BudgetItem("门票", None, 1, None),
        ],
        set(),
    )
    assert outcome.ok
    assert outcome.total_known_cents == 80000
    assert outcome.total_known_yuan == "800.00"
    assert outcome.unknown == [{"name": "门票", "reason": "未提供价格"}]
    assert outcome.items[0]["source"] == "user"


def test_user_price_without_link_is_counted():
    outcome = estimate_budget_cents(1, 0, [BudgetItem("零食", Decimal("12.5"), 2, None)], set())
    assert outcome.ok
    assert outcome.total_known_cents == 2500


def test_extra_decimal_places_are_rejected():
    outcome = estimate_budget_cents(1, 0, [BudgetItem("票", Decimal("1.005"), 1, None)], set())
    assert outcome.ok is False
    assert outcome.error == "金额最多两位小数"


def test_unknown_source_keeps_last_budget(client):
    state = load_state()
    state.last_budget_cents = 123
    state.last_budget = {"people": 1, "nights": 0, "items": [], "unknown": []}
    save_state(state)
    fresh = load_state()
    result = apply_budget(fresh, 1, 0, [BudgetItem("票", Decimal("10"), 1, "https://evil.example/x")])
    assert result == {"ok": False, "error": "source_url 不在本会话搜索结果中"}
    assert fresh.last_budget_cents == 123
    assert load_state().last_budget_cents == 123


def test_fifth_search_slot_does_not_call_client(client, monkeypatch):
    state = RequestState()
    for _ in range(4):
        assert consume_search_slot(state) is None
    assert consume_search_slot(state) == "已达搜索次数"
    assert state.search_count == 4

    calls = []
    monkeypatch.setattr("app.clients.brave_search", lambda *_args, **_kwargs: calls.append(1))
    current = begin_request("search-slot")
    current.search_count = 4
    runtime = SimpleNamespace(context=SimpleNamespace(request_id="search-slot"))
    try:
        payload = json.loads(web_search.func(query="青岛博物馆", runtime=runtime))
    finally:
        end_request("search-slot")
    assert payload["error"] == "已达搜索次数"
    assert calls == []


def _ready():
    state = begin_request("save")
    state.seen_urls.add("https://example.org/museum")
    state.url_titles["https://example.org/museum"] = "青岛市博物馆"
    state.weather_lines["2026-09-26"] = weather_phrase(0, 15.2, 22.4)
    state.routes[route_key(36.0671, 120.3826, 36.07, 120.39, "driving")] = (10, 2.5)
    state.last_budget_cents = 80000
    state.last_budget = {
        "people": 2,
        "nights": 2,
        "items": [
            {
                "name": "酒店",
                "price_yuan": "400.00",
                "quantity": 2,
                "source": "user",
                "source_url": None,
                "cents": 80000,
            }
        ],
        "unknown": [{"name": "门票", "reason": "未提供价格"}],
    }
    save_state(state)
    runtime = SimpleNamespace(context=SimpleNamespace(request_id="save"))
    return runtime


def _day(line: str, stops: int = 1, minutes: int = 10):
    return {
        "date": "2026-09-26",
        "weather_line": line,
        "stops": [
            {"period": "上午", "name": f"站点{index}", "category": "景点"} for index in range(stops)
        ],
        "legs": [
            {
                "from_name": "青岛",
                "to_name": "青岛市博物馆",
                "from_latitude": 36.0671,
                "from_longitude": 120.3826,
                "to_latitude": 36.07,
                "to_longitude": 120.39,
                "mode": "driving",
                "minutes": minutes,
                "kilometers": 2.5,
            }
        ]
        if minutes != 10 or stops > 1
        else [],
    }


def _save(runtime, **overrides):
    line = weather_phrase(0, 15.2, 22.4)
    payload = {
        "title": "青岛一日",
        "cities": ["青岛"],
        "people": 2,
        "days": [_day(line)],
        "total_known_yuan": "800.00",
        "unknown_items": ["门票"],
        "sources": [],
    }
    payload.update(overrides)
    return json.loads(
        save_itinerary.func(
            title=payload["title"],
            cities=payload["cities"],
            people=payload["people"],
            days=payload["days"],
            total_known_yuan=payload["total_known_yuan"],
            unknown_items=payload["unknown_items"],
            sources=payload["sources"],
            runtime=runtime,
        )
    )


def test_save_rejects_bad_totals_shape_weather_and_route(client):
    runtime = _ready()
    line = weather_phrase(0, 15.2, 22.4)
    assert validate_itinerary(
        {
            "title": "青岛一日",
            "cities": ["青岛", "济南", "烟台"],
            "people": 2,
            "days": [_day(line)],
            "total_known_yuan": "800.00",
            "unknown_items": ["门票"],
            "sources": [],
        },
        load_state(),
    ) == "城市需为 1 到 2 座"
    cases = [
        {"total_known_yuan": "800.01"},
        {"cities": ["青岛", "济南", "烟台"]},
        {"days": [_day(line) for _ in range(6)]},
        {"days": [_day(line, stops=4)]},
        {"days": [_day("阴 1–2°C")]},
        {"days": [_day(line, minutes=11)]},
    ]
    for overrides in cases:
        result = _save(runtime, **overrides)
        assert result["ok"] is False, overrides
    assert client.get("/api/v1/itineraries").json()["itineraries"] == []
    end_request("save")
