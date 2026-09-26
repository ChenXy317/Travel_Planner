"""读取和更新对话模型。密钥只进不出。"""

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.llm_runtime import get_llm_config, set_llm_config

log = logging.getLogger("app.chat")
router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


class LLMConfigUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=192)
    api_key: str | None = Field(default=None, max_length=512)


def list_models(base_url: str, api_key: str | None) -> list[str]:
    """读 OpenAI 兼容的 /models。失败时当成本机没有可列的模型。"""
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    try:
        with httpx.Client(timeout=3) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        log.info("读取模型列表失败")
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    names: list[str] = []
    if not isinstance(rows, list):
        return names
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _to_out() -> dict:
    cfg = get_llm_config()
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "api_key_set": bool((cfg.api_key or "").strip()),
        "local_models": list_models(cfg.base_url, cfg.api_key),
    }


@router.get("/config")
def read_llm_config() -> dict:
    return _to_out()


@router.put("/config")
def update_llm_config(body: LLMConfigUpdate):
    try:
        set_llm_config(base_url=body.base_url, model=body.model, api_key=body.api_key)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    cfg = get_llm_config()
    log.info("更新对话模型 %s @ %s", cfg.model, urlparse(cfg.base_url).hostname or "")
    return _to_out()
