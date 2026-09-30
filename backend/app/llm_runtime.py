"""对话模型覆盖。只活在进程内存里，重启后回到环境变量。"""

import ipaddress
import socket
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse

from app.config import get_settings

_ALLOWED_LLM_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_BLOCKED_LLM_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.com",
        "169.254.169.254",
    }
)
_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"


@dataclass
class LLMRuntimeConfig:
    base_url: str
    api_key: str | None
    model: str


_lock = Lock()
_override: LLMRuntimeConfig | None = None


def env_chat_config() -> LLMRuntimeConfig:
    settings = get_settings()
    base = settings.llm_base_url.strip() or _DEFAULT_BASE_URL
    key = settings.llm_api_key.strip() or None
    return LLMRuntimeConfig(
        base_url=base.rstrip("/"),
        api_key=key,
        model=settings.llm_model.strip(),
    )


class ModelConfigError(ValueError):
    """对话模型的名字或地址还不能用。"""


def _literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(host)
    except ValueError:
        try:
            # ipaddress 不认八进制、十六进制和省略写法，本机解析会连到那个地址。
            addr = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _address_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.is_loopback:
        return False
    return bool(
        addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _host_blocked(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if host in _ALLOWED_LLM_HOSTS:
        return False
    if host in _BLOCKED_LLM_HOSTS:
        return True
    addr = _literal_address(host)
    if addr is not None:
        return _address_blocked(addr)
    return "." not in host


def ensure_chat_config(cfg: LLMRuntimeConfig) -> None:
    if not cfg.model.strip():
        raise ModelConfigError("需要填写模型名")
    parsed = urlparse(cfg.base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelConfigError("base_url 必须是带主机名的 http(s) 地址")


def validate_llm_base_url(url: str) -> str:
    """只接受公网或本机回环的 http(s) 地址，拒绝内网和云元数据。"""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是带主机名的 http(s) 地址")
    host = parsed.hostname or ""
    if not host or _host_blocked(host):
        raise ValueError("base_url 主机不允许")
    return url.strip().rstrip("/")


def get_llm_config() -> LLMRuntimeConfig:
    with _lock:
        override = _override
    if override is None:
        return env_chat_config()
    return LLMRuntimeConfig(
        base_url=override.base_url,
        api_key=override.api_key,
        model=override.model,
    )


def set_llm_config(
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
) -> LLMRuntimeConfig:
    env = env_chat_config()
    current = get_llm_config()
    new_url = validate_llm_base_url(base_url or current.base_url)
    new_model = (model or current.model).strip()
    if not new_model:
        raise ValueError("需要填写模型名")

    if api_key is not None and api_key.strip() == "":
        key = None
    elif api_key is not None:
        key = api_key
    else:
        key = current.api_key
        env_key = (env.api_key or "").strip()
        if new_url != current.base_url and (key or "").strip() == env_key:
            key = None

    cfg = LLMRuntimeConfig(base_url=new_url, api_key=key, model=new_model)
    global _override
    with _lock:
        _override = cfg
    return cfg


def clear_llm_overrides() -> None:
    global _override
    with _lock:
        _override = None


def effective_api_key(cfg: LLMRuntimeConfig | None = None) -> str:
    current = cfg or get_llm_config()
    key = (current.api_key or "").strip()
    return key if key else "sk-no-auth"
