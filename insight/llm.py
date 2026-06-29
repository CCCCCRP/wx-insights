"""OpenAI 兼容 LLM 客户端（本地 Ollama + 云端 DeepSeek 等）。"""
from __future__ import annotations

import logging
from typing import Literal, Type, TypeVar

import httpx
import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from worker.insight.config import InsightSettings, load_insight_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
LLMBackend = Literal["local", "cloud"]

# 复用 client，避免并发 Phase A 时反复建连；本地请求必须 trust_env=False 绕过 SOCKS 代理
_openai_clients: dict[tuple[str, str, str], AsyncOpenAI] = {}
_instructor_clients: dict[tuple[str, str, str], instructor.Instructor] = {}


def _credentials(settings: InsightSettings, backend: LLMBackend) -> tuple[str, str]:
    if backend == "local":
        return settings.local_llm_base_url, settings.local_llm_api_key or "ollama"
    return settings.cloud_llm_base_url, settings.cloud_llm_api_key


def _client_cache_key(settings: InsightSettings, backend: LLMBackend) -> tuple[str, str, str]:
    base_url, api_key = _credentials(settings, backend)
    return backend, base_url, api_key


def _make_http_client(backend: LLMBackend) -> httpx.AsyncClient:
    """本地 Ollama 不走系统代理；云端默认也不继承 SOCKS（DeepSeek 直连）。"""
    timeout = httpx.Timeout(600.0 if backend == "local" else 180.0, connect=15.0)
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def ensure_backend_configured(
    settings: InsightSettings | None = None,
    *,
    backend: LLMBackend = "cloud",
) -> InsightSettings:
    settings = settings or load_insight_settings()
    if backend == "local":
        if not settings.local_llm_base_url:
            raise RuntimeError(
                "本地 LLM base_url 未配置。"
                "请设置 OLLAMA_LLM_BASE_URL 或 insight.yaml llm.local.base_url"
            )
        return settings
    if not settings.cloud_llm_api_key:
        raise RuntimeError(
            "云端 LLM API Key 未设置。"
            "请配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY。"
        )
    return settings


def ensure_llm_configured(settings: InsightSettings | None = None) -> InsightSettings:
    """兼容旧调用：至少云端可用。"""
    return ensure_backend_configured(settings, backend="cloud")


def format_prompt(
    prompt: str,
    *,
    backend: LLMBackend,
    model: str,
    no_think: bool | None = None,
) -> str:
    """Qwen3 本地推理时追加 /no_think，关闭思考链以提速。"""
    use_no_think = no_think if no_think is not None else (
        backend == "local" and "qwen3" in model.lower()
    )
    if use_no_think and "/no_think" not in prompt:
        return f"{prompt.rstrip()}\n/no_think"
    return prompt


def get_openai_client(
    settings: InsightSettings | None = None,
    *,
    backend: LLMBackend = "cloud",
) -> AsyncOpenAI:
    settings = ensure_backend_configured(settings, backend=backend)
    cache_key = _client_cache_key(settings, backend)
    if cache_key in _openai_clients:
        return _openai_clients[cache_key]

    base_url, api_key = _credentials(settings, backend)
    kwargs: dict = {
        "api_key": api_key,
        "http_client": _make_http_client(backend),
    }
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncOpenAI(**kwargs)
    _openai_clients[cache_key] = client
    return client


def get_instructor_client(
    settings: InsightSettings | None = None,
    *,
    backend: LLMBackend = "cloud",
):
    settings = ensure_backend_configured(settings, backend=backend)
    cache_key = _client_cache_key(settings, backend)
    if cache_key in _instructor_clients:
        return _instructor_clients[cache_key]

    client = instructor.from_openai(
        get_openai_client(settings, backend=backend),
        mode=instructor.Mode.JSON,
    )
    _instructor_clients[cache_key] = client
    return client


async def chat_completion(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 8192,
    settings: InsightSettings | None = None,
    backend: LLMBackend = "cloud",
) -> tuple[str, dict]:
    """非结构化文本生成（Phase C）。"""
    settings = ensure_backend_configured(settings, backend=backend)
    client = get_openai_client(settings, backend=backend)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    text = (response.choices[0].message.content or "").strip()
    usage = {}
    if response.usage:
        usage = {
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        }
    return text, usage


async def structured_completion(
    prompt: str,
    response_model: Type[T],
    *,
    model: str,
    max_tokens: int = 4096,
    max_tokens_ceiling: int = 65536,
    settings: InsightSettings | None = None,
    backend: LLMBackend = "cloud",
    no_think: bool | None = None,
    max_retries: int = 2,
) -> T:
    """结构化 JSON 输出（Phase A / B / Profile）。"""
    settings = ensure_backend_configured(settings, backend=backend)
    client = get_instructor_client(settings, backend=backend)
    content = format_prompt(prompt, backend=backend, model=model, no_think=no_think)

    tokens = max_tokens
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=tokens,
                response_model=response_model,
            )
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if attempt < max_retries and (
                "incomplete" in msg or "max_tokens" in msg or "length limit" in msg
            ):
                prev = tokens
                tokens = min(tokens * 2, max_tokens_ceiling)
                logger.warning(
                    "结构化输出被截断，max_tokens %d → %d 重试 (%d/%d)",
                    prev,
                    tokens,
                    attempt + 1,
                    max_retries,
                )
                continue
            raise
    assert last_err is not None
    raise last_err
