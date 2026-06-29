"""Embedding 层：本地 Ollama（默认 bge-m3）→ pgvector。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

import httpx

from worker.db import insight_repo
from worker.insight.config import InsightSettings, load_insight_settings

logger = logging.getLogger(__name__)

# SentenceTransformer 模型缓存（避免每次 batch 重新加载）
_ST_MODEL_CACHE: dict[str, Any] = {}


def is_configured(settings: Optional[InsightSettings] = None) -> bool:
    settings = settings or load_insight_settings()
    return settings.embedding_backend in ("ollama", "openai", "sentence_transformers")


def _is_ollama_context_length_error(resp: httpx.Response) -> bool:
    if resp.status_code != 400:
        return False
    try:
        err = str(resp.json().get("error", "")).lower()
    except Exception:
        return False
    return "context length" in err


async def _embed_ollama_request(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    texts: List[str],
) -> List[List[float]]:
    """向 Ollama 发 embed；超长时拆批或截断单条后重试。"""
    if not texts:
        return []

    resp = await client.post(url, json={"model": model, "input": texts})
    if _is_ollama_context_length_error(resp):
        if len(texts) > 1:
            mid = len(texts) // 2
            logger.debug("Ollama 上下文超限，拆批 %d -> %d + %d", len(texts), mid, len(texts) - mid)
            left = await _embed_ollama_request(client, url, model, texts[:mid])
            right = await _embed_ollama_request(client, url, model, texts[mid:])
            return left + right

        text = texts[0]
        floor = 500
        if len(text) <= floor:
            resp.raise_for_status()
        new_len = max(floor, len(text) * 3 // 4)
        logger.warning("单条正文超长，截断重试: %d -> %d chars", len(text), new_len)
        return await _embed_ollama_request(client, url, model, [text[:new_len]])

    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings") or []
    if len(embeddings) != len(texts):
        raise RuntimeError(f"Ollama embed 返回数量不匹配: {len(embeddings)} != {len(texts)}")
    return embeddings


async def _embed_ollama(
    texts: List[str],
    settings: InsightSettings,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> List[List[float]]:
    if not texts:
        return []
    url = f"{settings.ollama_base_url}/api/embed"

    async def _do(c: httpx.AsyncClient) -> List[List[float]]:
        vecs = await _embed_ollama_request(c, url, settings.embedding_model, texts)
        for vec in vecs:
            if len(vec) != settings.embedding_dimensions:
                raise RuntimeError(
                    f"向量维度 {len(vec)} != 配置 {settings.embedding_dimensions}。"
                    "请检查 Ollama 模型是否与 insight.yaml embedding.dimensions 一致。"
                )
        return vecs

    if client is not None:
        return await _do(client)
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
        return await _do(c)


async def _embed_openai(texts: List[str], settings: InsightSettings) -> List[List[float]]:
    from openai import AsyncOpenAI
    api_key = settings.llm_api_key or __import__("os").getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI embedding 需要 OPENAI_API_KEY 或 LLM_API_KEY")
    c = AsyncOpenAI(api_key=api_key)
    resp = await c.embeddings.create(
        model=settings.embedding_model,
        input=texts,
        encoding_format="float",
        dimensions=settings.embedding_dimensions,
    )
    return [item.embedding for item in resp.data]


def _embed_sentence_transformers(texts: List[str], settings: InsightSettings) -> List[List[float]]:
    key = settings.embedding_model
    if key not in _ST_MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        logger.info("加载 SentenceTransformer 模型: %s", key)
        _ST_MODEL_CACHE[key] = SentenceTransformer(key)
    model = _ST_MODEL_CACHE[key]
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


async def embed_texts(
    texts: List[str],
    settings: Optional[InsightSettings] = None,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[List[float]]:
    """通用 embed 入口；http_client 可由 embed_all 注入以复用连接。"""
    settings = settings or load_insight_settings()
    if not texts:
        return []

    backend = settings.embedding_backend
    if backend == "ollama":
        return await _embed_ollama(texts, settings, client=http_client)
    if backend == "openai":
        return await _embed_openai(texts, settings)
    if backend == "sentence_transformers":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _embed_sentence_transformers, texts, settings)
    raise RuntimeError(f"未知 embedding backend: {backend}")


async def check_ollama_available(settings: Optional[InsightSettings] = None) -> bool:
    settings = settings or load_insight_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def embed_summaries_batch(
    settings: InsightSettings,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> int:
    rows = insight_repo.fetch_summaries_missing_embedding(limit=settings.embedding_batch_size)
    if not rows:
        return 0

    texts = [r["summary"][: settings.embedding_summary_truncate] for r in rows]
    vecs = await embed_texts(texts, settings, http_client=http_client)

    if len(vecs) != len(rows):
        raise RuntimeError(
            f"embed 返回向量数 {len(vecs)} 与输入 {len(rows)} 不匹配，跳过本批写入"
        )

    pairs = [(vec, row["aid"]) for row, vec in zip(rows, vecs)]
    insight_repo.update_summary_embeddings_batch(pairs)
    return len(rows)


async def embed_articles_batch(
    settings: InsightSettings,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> int:
    rows = insight_repo.fetch_articles_missing_content_embedding(limit=settings.embedding_batch_size)
    if not rows:
        return 0

    from worker.insight.content_clean import clean_for_embed
    texts = [
        clean_for_embed(
            r["plain_content"],
            fakeid=r.get("fakeid", ""),
            truncate=settings.embedding_content_truncate,
        )
        for r in rows
    ]
    vecs = await embed_texts(texts, settings, http_client=http_client)

    if len(vecs) != len(rows):
        raise RuntimeError(
            f"embed 返回向量数 {len(vecs)} 与输入 {len(rows)} 不匹配，跳过本批写入"
        )

    pairs = [(vec, row["aid"]) for row, vec in zip(rows, vecs)]
    insight_repo.update_article_content_embeddings_batch(pairs)
    return len(rows)


async def embed_all(settings: Optional[InsightSettings] = None) -> dict:
    settings = settings or load_insight_settings()
    if settings.embedding_backend == "ollama":
        if not await check_ollama_available(settings):
            raise RuntimeError(
                f"Ollama 未运行或不可达: {settings.ollama_base_url}。"
                f"请先执行: ollama pull {settings.embedding_model}"
            )

    total_summaries = 0
    total_articles = 0

    # 复用单个 httpx.AsyncClient（仅 Ollama 需要；其他 backend 忽略此参数）
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as http_client:
        client_arg = http_client if settings.embedding_backend == "ollama" else None

        batch_num = 0
        while True:
            n = await embed_summaries_batch(settings, http_client=client_arg)
            total_summaries += n
            if n == 0:
                break
            batch_num += 1
            logger.info("摘要 embed 进度: 本批 %d 条，累计 %d 条", n, total_summaries)

        batch_num = 0
        while True:
            n = await embed_articles_batch(settings, http_client=client_arg)
            total_articles += n
            if n == 0:
                break
            batch_num += 1
            logger.info("正文 embed 进度: 本批 %d 篇，累计 %d 篇", n, total_articles)

    logger.info("Embed 完成: 摘要 %d 条，正文 %d 篇", total_summaries, total_articles)
    return {"summaries": total_summaries, "articles": total_articles}


def run_embed_all(settings: Optional[InsightSettings] = None) -> dict:
    return asyncio.run(embed_all(settings))


async def embed_single_summary(
    aid: str,
    summary: str,
    settings: Optional[InsightSettings] = None,
) -> List[float]:
    settings = settings or load_insight_settings()
    [vec] = await embed_texts([summary[: settings.embedding_summary_truncate]], settings)
    insight_repo.update_summary_embedding(aid, vec)
    return vec
