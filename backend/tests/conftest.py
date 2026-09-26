import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def offline_network():
    """测试默认不打外网，并把上海的今天固定在 2026-09-26。"""
    from app import clients
    from app.checks import set_today
    from app.session import _requests

    def deny(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"测试禁止访问网络 {request.url}")

    clients.set_transport(httpx.MockTransport(deny))
    clients.use_test_clock()
    clock = {"now": 0.0}
    clients.gate.configure(lambda: clock["now"], lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    set_today(date(2026, 9, 26))
    _requests.clear()
    yield
    set_today(None)
    _requests.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    import app.db as db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "travel.db")
    monkeypatch.setattr(config, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.reset_engine()
    from app.llm_runtime import clear_llm_overrides
    from app.main import app
    import app.llm_config as llm_config

    clear_llm_overrides()
    monkeypatch.setattr(llm_config, "list_models", lambda *_args, **_kwargs: [])

    with TestClient(app) as test_client:
        yield test_client
    clear_llm_overrides()
    db.reset_engine()
