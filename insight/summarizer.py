"""Phase A：单篇摘要 → article_summaries（含文件缓存 fallback）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import List, Optional

from worker.db import insight_repo
from worker.db.connection import database_available
from worker.insight.config import InsightSettings, load_insight_settings
from worker.insight.embedder import embed_single_summary, is_configured as embed_configured
from worker.insight.llm import structured_completion
from worker.insight.models import ArticleRecord, ArticleSummaryRecord, SummaryOutput
from worker.insight.prompts import PHASE_A_PROMPT, _LENS_GUIDE

logger = logging.getLogger(__name__)


def content_hash(plain_content: str, truncate: int) -> str:
    return hashlib.md5(plain_content[:truncate].encode("utf-8")).hexdigest()


async def summarize_one(
    article: ArticleRecord,
    settings: Optional[InsightSettings] = None,
) -> ArticleSummaryRecord:
    settings = settings or load_insight_settings()
    truncate = settings.phase_a_content_truncate
    from worker.insight.content_clean import clean_for_embed
    plain = clean_for_embed(article.plain_content, fakeid=article.fakeid or "", truncate=truncate)

    prompt = PHASE_A_PROMPT.format(
        nickname=article.nickname or "未知",
        account_lens=article.account_lens,
        title=article.title,
        plain_content=plain,
        truncate=truncate,
        lens_guide=_LENS_GUIDE,
    )

    result: SummaryOutput = await structured_completion(
        prompt,
        SummaryOutput,
        model=settings.phase_a_model,
        max_tokens=settings.phase_a_max_tokens,
        settings=settings,
        backend=settings.phase_a_backend,
        no_think=settings.phase_a_no_think,
    )

    ch = content_hash(article.plain_content, truncate)
    record = ArticleSummaryRecord(
        aid=article.aid,
        fakeid=article.fakeid,
        nickname=article.nickname,
        title=article.title,
        link=article.link,
        publish_time=article.publish_time,
        summary=result.summary,
        topic_tags=result.topic_tags[:5],
        claims=result.claims[:3],
        sentiment=result.sentiment,
        quality_score=result.quality_score,
        account_lens=article.account_lens,
        model=settings.phase_a_model,
        content_hash=ch,
    )

    embedding: Optional[List[float]] = None
    if embed_configured(settings):
        try:
            embedding = await embed_single_summary(record.aid, record.summary, settings)
            record.summary_embedding = embedding
        except Exception as e:
            logger.warning("摘要 embedding 失败 aid=%s: %s", record.aid, e)

    _persist_summary(record, settings)
    return record


async def _summarize_with_retry(article: ArticleRecord, settings: InsightSettings) -> Optional[ArticleSummaryRecord]:
    last_err: Optional[Exception] = None
    for attempt in range(settings.phase_a_retry + 1):
        try:
            return await summarize_one(article, settings)
        except Exception as e:
            last_err = e
            logger.warning("摘要失败 aid=%s attempt=%d: %s", article.aid, attempt + 1, e)
            await asyncio.sleep(2 ** attempt)
    logger.error("摘要最终失败 aid=%s: %s", article.aid, last_err)
    return None


async def summarize_batch(
    articles: List[ArticleRecord],
    settings: Optional[InsightSettings] = None,
) -> List[ArticleSummaryRecord]:
    settings = settings or load_insight_settings()
    sem = asyncio.Semaphore(settings.phase_a_max_concurrency)
    results: List[ArticleSummaryRecord] = []

    async def _run(art: ArticleRecord) -> None:
        async with sem:
            rec = await _summarize_with_retry(art, settings)
            if rec:
                results.append(rec)

    await asyncio.gather(*[_run(a) for a in articles])
    return results


def _persist_summary(record: ArticleSummaryRecord, settings: InsightSettings) -> None:
    if database_available():
        try:
            insight_repo.upsert_summary(
                aid=record.aid,
                fakeid=record.fakeid,
                summary=record.summary,
                topic_tags=record.topic_tags,
                claims=record.claims,
                sentiment=record.sentiment,
                quality_score=record.quality_score,
                account_lens=record.account_lens,
                content_hash=record.content_hash,
                model=record.model,
                summary_embedding=record.summary_embedding,
            )
            return
        except Exception as e:
            logger.warning("DB 写入摘要失败，写文件缓存: %s", e)

    cache_dir = settings.summary_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{record.aid}.json"
    path.write_text(record.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")


async def ensure_summaries_for_primary(
    primary: List[ArticleRecord],
    settings: Optional[InsightSettings] = None,
) -> List[ArticleSummaryRecord]:
    """确保 Primary 文章均有摘要。"""
    settings = settings or load_insight_settings()
    existing = load_primary_summaries(primary, settings)
    existing_map = {s.aid: s for s in existing}

    to_summarize: List[ArticleRecord] = []
    for art in primary:
        if art.low_confidence or not art.plain_content or not art.aid:
            continue
        cached = existing_map.get(art.aid)
        ch = content_hash(art.plain_content, settings.phase_a_content_truncate)
        if cached and cached.content_hash == ch:
            continue
        to_summarize.append(art)

    if to_summarize:
        logger.info("Phase A: 待摘要 %d 篇", len(to_summarize))
        await summarize_batch(to_summarize, settings)

    return load_primary_summaries(primary, settings)


def load_primary_summaries(
    primary: List[ArticleRecord],
    settings: Optional[InsightSettings] = None,
) -> List[ArticleSummaryRecord]:
    settings = settings or load_insight_settings()
    aids = [p.aid for p in primary if p.aid]
    summaries: List[ArticleSummaryRecord] = []

    if database_available() and aids:
        try:
            rows = insight_repo.fetch_summaries_for_aids(aids)
            summaries = [_row_to_summary(r) for r in rows]
        except Exception as e:
            logger.warning("DB 加载摘要失败: %s", e)

    if not summaries:
        for art in primary:
            cached = _load_cache(art.aid, settings)
            if cached:
                summaries.append(cached)

    return [s for s in summaries if s.quality_score >= 0.0]


def _load_cache(aid: str, settings: InsightSettings) -> Optional[ArticleSummaryRecord]:
    path = settings.summary_cache_dir / f"{aid}.json"
    if not path.is_file():
        return None
    try:
        return ArticleSummaryRecord(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _row_to_summary(row: dict) -> ArticleSummaryRecord:
    emb = row.get("summary_embedding")
    if emb is not None and hasattr(emb, "tolist"):
        emb = emb.tolist()
    return ArticleSummaryRecord(
        aid=row["aid"],
        fakeid=row.get("fakeid") or "",
        nickname=row.get("nickname") or "",
        title=row.get("title") or "",
        link=row.get("link") or "",
        publish_time=int(row.get("publish_time") or 0),
        summary=row.get("summary") or "",
        topic_tags=list(row.get("topic_tags") or []),
        claims=row.get("claims") or [],
        sentiment=row.get("sentiment") or "neutral",
        quality_score=float(row.get("quality_score") or 0.5),
        account_lens=row.get("account_lens") or "general",
        summary_embedding=emb,
        model=row.get("model") or "",
        content_hash=row.get("content_hash") or "",
    )
