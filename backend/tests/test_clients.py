import json
from datetime import timedelta

import httpx

from app import clients
from tests.conftest import load_fixture


def _transport(handler):
    clients.set_transport(httpx.MockTransport(handler))


def test_llm_context_and_web_search_share_shape(client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json=load_fixture("brave_llm_context.json"))
        return httpx.Response(404, text="missing")

    _transport(handler)
    primary = clients.brave_search("青岛博物馆", "ALL", "zh-hans")
    assert primary["ok"] is True
    museum = primary["results"][0]
    assert museum["title"] == "青岛市博物馆"
    assert museum["url"] == "https://example.org/museum"
    assert len(museum["snippets"]) == 3
    assert "第四段应丢弃" not in json.dumps(primary, ensure_ascii=False)
    long = next(item for item in primary["results"] if item["url"].endswith("/long"))
    assert len(long["snippets"][0]) == 400
    assert "<b>" not in long["snippets"][0]
    assert all(item["url"].startswith("https://") for item in primary["results"])
    notitle = next(item for item in primary["results"] if item["url"].endswith("/notitle"))
    assert notitle["title"] == "example.org"

    def fallback(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "llm/context" in str(request.url):
            return httpx.Response(404, json={"error": "missing"})
        assert "maximum_number_of_tokens" not in str(request.url)
        return httpx.Response(200, json=load_fixture("brave_web_search.json"))

    calls.clear()
    clients.now_clock.current += timedelta(days=2)
    _transport(fallback)
    web = clients.brave_search("另一条查询", "ALL", "zh-hans")
    assert web["ok"] is True
    assert len(web["results"]) == 1
    assert web["results"][0]["snippets"] == ["周二至周日 9:00-17:00"]
    assert len(calls) == 2


def test_empty_grounding_is_not_a_successful_empty_list(client):
    _transport(lambda _request: httpx.Response(200, json=load_fixture("brave_llm_context_empty.json")))
    result = clients.brave_search("空", "ALL", "zh-hans")
    assert result == {"ok": False, "error": "搜索无结果"}


def test_auth_rate_limit_and_timeout_do_not_fall_back(client, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(401, json={"error": "nope"})
        if len(calls) == 2:
            return httpx.Response(429, json={"error": "rate limit"})
        raise httpx.ReadTimeout("timed out")

    _transport(handler)
    assert clients.brave_search("甲", "ALL", "zh-hans")["error"] == "搜索未授权"
    assert clients.brave_search("乙", "ALL", "zh-hans")["error"] == "搜索请求过于频繁"
    assert clients.brave_search("丙", "ALL", "zh-hans")["error"] == "搜索超时"
    assert len(calls) == 3
    assert all("llm/context" in url for url in calls)
    assert all("web/search" not in url for url in calls)

    monkeypatch.setenv("SEARCH_API_KEY", "")
    before = len(calls)
    assert clients.brave_search("丁", "ALL", "zh-hans")["error"] == "未配置 SEARCH_API_KEY"
    assert len(calls) == before


def test_plan_error_falls_back_to_web_search(client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "llm/context" in str(request.url):
            return httpx.Response(403, json=load_fixture("brave_plan_error.json"))
        return httpx.Response(200, json=load_fixture("brave_web_search.json"))

    _transport(handler)
    result = clients.brave_search("套餐", "ALL", "zh-hans")
    assert result["ok"] is True
    assert result["results"][0]["snippets"] == ["周二至周日 9:00-17:00"]
    assert "llm/context" in calls[0]
    assert "web/search" in calls[1]


def test_search_cache_expires_after_one_day(client):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=load_fixture("brave_llm_context.json"))

    _transport(handler)
    assert clients.brave_search("缓存", "ALL", "zh-hans")["ok"] is True
    assert clients.brave_search("缓存", "ALL", "zh-hans")["ok"] is True
    assert calls["n"] == 1
    clients.now_clock.current += timedelta(hours=25)
    assert clients.brave_search("缓存", "ALL", "zh-hans")["ok"] is True
    assert calls["n"] == 2


def test_gate_spaces_calls_without_overlap():
    import threading
    import time

    seen = []

    def work():
        seen.append(clients.gate.depth)
        time.sleep(0.05)

    threads = [threading.Thread(target=lambda: clients.gate.call(work)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert clients.gate.max_depth == 1
    assert seen == [1, 1]
    assert clients.gate.waits[1] >= 1
