import httpx

from app.agent import build_chat_model
from app.llm_config import list_models
from app.llm_runtime import clear_llm_overrides, validate_llm_base_url


def _env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:1.5b")
    monkeypatch.setenv("LLM_API_KEY", "env-secret")
    clear_llm_overrides()


def test_config_does_not_return_key(client, monkeypatch):
    _env(monkeypatch)
    response = client.get("/api/v1/llm/config")
    body = response.json()
    assert response.status_code == 200
    assert body["base_url"] == "http://127.0.0.1:11434/v1"
    assert body["model"] == "qwen2.5:1.5b"
    assert body["api_key_set"] is True
    assert "env-secret" not in response.text


def test_omitted_key_is_kept_and_blank_key_clears(client, monkeypatch):
    _env(monkeypatch)
    kept = client.put(
        "/api/v1/llm/config",
        json={"base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:1.5b"},
    )
    assert kept.json()["api_key_set"] is True
    assert "env-secret" not in kept.text
    cleared = client.put(
        "/api/v1/llm/config",
        json={"base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:1.5b", "api_key": ""},
    )
    assert cleared.json()["api_key_set"] is False


def test_changing_url_drops_env_key_but_keeps_custom_key(client, monkeypatch):
    _env(monkeypatch)
    dropped = client.put(
        "/api/v1/llm/config",
        json={"base_url": "https://api.example.com/v1", "model": "gpt"},
    )
    assert dropped.status_code == 200
    assert dropped.json()["api_key_set"] is False

    clear_llm_overrides()
    client.put(
        "/api/v1/llm/config",
        json={
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen2.5:1.5b",
            "api_key": "custom-secret",
        },
    )
    kept = client.put(
        "/api/v1/llm/config",
        json={"base_url": "https://api.example.com/v1", "model": "gpt"},
    )
    assert kept.json()["api_key_set"] is True
    assert "custom-secret" not in kept.text


def test_private_and_metadata_hosts_are_rejected(client):
    private = client.put(
        "/api/v1/llm/config",
        json={"base_url": "http://192.168.1.9/v1", "model": "m"},
    )
    metadata = client.put(
        "/api/v1/llm/config",
        json={"base_url": "http://169.254.169.254/latest", "model": "m"},
    )
    assert private.status_code == 400
    assert metadata.status_code == 400
    assert private.json()["error"] == "base_url 主机不允许"
    assert validate_llm_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"


def test_list_models_reads_openai_ids(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "qwen2.5:1.5b"}, {"id": ""}, "skip"]}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 3

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, headers=None):
            assert url == "http://127.0.0.1:11434/v1/models"
            assert headers == {"Authorization": "Bearer k"}
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert list_models("http://127.0.0.1:11434/v1", "k") == ["qwen2.5:1.5b"]


def test_build_chat_model_uses_page_config(monkeypatch):
    _env(monkeypatch)
    from app.llm_runtime import set_llm_config

    set_llm_config(base_url="https://api.example.com/v1", model="demo", api_key="page-key")
    model = build_chat_model()
    assert model.model_name == "demo"
    assert model.temperature == 0.2
    assert str(model.openai_api_base).rstrip("/") == "https://api.example.com/v1"
