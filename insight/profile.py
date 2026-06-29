"""Phase 1.5：账号自动画像。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from worker.insight.config import InsightSettings, load_insight_settings
from worker.insight.llm import ensure_backend_configured, structured_completion
from worker.insight.models import AccountProfileOutput
from worker.crawl.week import HISTORY_BACKFILL_DAYS
from worker.db import insight_repo
from worker.db.connection import database_available
from worker.insight.prompts import BOOTSTRAP_PROFILE_PROMPT, PROFILE_PROMPT
from worker.insight.tags import sync_profiles_to_db, write_profile_to_yaml

logger = logging.getLogger(__name__)
TZ_CN = timezone(timedelta(hours=8))


def can_profile(
    account: dict,
    since_ts: int,
    min_summaries: int,
    min_titles: int,
) -> bool:
    if not account.get("year_backfill_done"):
        return False
    if account.get("insight_profile_locked"):
        return False
    if not database_available():
        return False
    fakeid = account["fakeid"]
    summary_count = insight_repo.count_quality_summaries(fakeid, since_ts)
    if summary_count >= min_summaries:
        return True
    title_count = insight_repo.count_articles_for_account(fakeid, since_ts)
    return title_count >= min_titles


def needs_profiling(account: dict, recalibrate_days: int) -> bool:
    if account.get("insight_profile_locked"):
        return False
    profiled_at = account.get("insight_profiled_at")
    if profiled_at is None:
        return True
    if isinstance(profiled_at, datetime):
        if profiled_at.tzinfo is None:
            profiled_at = profiled_at.replace(tzinfo=TZ_CN)
        return (datetime.now(TZ_CN) - profiled_at).days >= recalibrate_days
    return True


async def profile_account(
    fakeid: str,
    settings: Optional[InsightSettings] = None,
    dry_run: bool = False,
) -> Optional[AccountProfileOutput]:
    settings = settings or load_insight_settings()
    ensure_backend_configured(settings, backend=settings.profile_backend)

    account = insight_repo.fetch_account_for_profile(fakeid)
    if not account:
        logger.warning("账号不存在: %s", fakeid)
        return None

    since_ts = int((datetime.now(TZ_CN) - timedelta(days=HISTORY_BACKFILL_DAYS)).timestamp())
    summaries = insight_repo.fetch_summaries_for_account(fakeid, since_ts)
    bootstrap = len(summaries) < settings.profile_min_summaries

    # 读取 2-3 篇正文头尾片段，供 strip 模板识别（不足时为空列表）
    raw_snippets_data = insight_repo.fetch_raw_content_snippets_for_account(fakeid, limit=3)
    if raw_snippets_data:
        snippet_parts = []
        for i, s in enumerate(raw_snippets_data, 1):
            snippet_parts.append(
                f"【文章{i}】{s['title']}\n"
                f"--- 开头 ---\n{s['head']}\n"
                f"--- 结尾 ---\n{s['tail']}"
            )
        raw_snippets = "\n\n".join(snippet_parts)
    else:
        raw_snippets = "（暂无正文内容，无法识别模板）"

    if bootstrap:
        titles = insight_repo.fetch_article_titles_for_account(
            fakeid, since_ts, limit=40,
        )
        if len(titles) < settings.profile_bootstrap_min_titles:
            logger.info(
                "摘要 %d、标题 %d，均不足，跳过画像: %s",
                len(summaries),
                len(titles),
                fakeid,
            )
            return None
        titles_compact = "\n".join(f"- {t}" for t in titles[:40])
        prompt = BOOTSTRAP_PROFILE_PROMPT.format(
            nickname=account.get("nickname") or fakeid,
            fakeid=fakeid,
            titles_compact=titles_compact,
            raw_snippets=raw_snippets,
        )
    else:
        compact_lines = []
        for s in summaries[:30]:
            tags = ", ".join(s.get("topic_tags") or [])
            compact_lines.append(f"- [{s['aid']}] {s['title']}: {s['summary'][:100]} (#{tags})")
        summaries_compact = "\n".join(compact_lines)
        prompt = PROFILE_PROMPT.format(
            nickname=account.get("nickname") or fakeid,
            fakeid=fakeid,
            summaries_compact=summaries_compact,
            raw_snippets=raw_snippets,
        )

    result: AccountProfileOutput = await structured_completion(
        prompt,
        AccountProfileOutput,
        model=settings.profile_model,
        max_tokens=settings.profile_max_tokens,
        settings=settings,
        backend=settings.profile_backend,
        no_think=settings.profile_no_think,
    )

    lens = result.insight_lens
    tags = result.insight_tags
    confidence = result.confidence
    if bootstrap:
        confidence = min(confidence, 0.65)
    if confidence < 0.6:
        lens = "general"
        logger.info("画像置信度低 (%.2f)，使用 general: %s", confidence, fakeid)

    if dry_run:
        mode = "bootstrap" if bootstrap else "full"
        logger.info(
            "[dry-run] %s (%s) → lens=%s tags=%s conf=%.2f",
            fakeid, mode, lens, tags, confidence,
        )
        return result

    insight_repo.update_account_profile(
        fakeid,
        insight_lens=lens,
        insight_tags=tags,
        confidence=confidence,
        source="auto_bootstrap" if bootstrap else "auto",
        strip_head_pattern=result.strip_head_pattern,
        strip_tail_markers=result.strip_tail_markers,
    )
    write_profile_to_yaml(fakeid, insight_lens=lens, insight_tags=tags)
    if result.strip_head_pattern or result.strip_tail_markers:
        logger.info(
            "写入 strip 规则: %s | head=%r tail=%s",
            fakeid,
            result.strip_head_pattern[:40] if result.strip_head_pattern else "",
            result.strip_tail_markers,
        )
    return result


async def run_profile_all(
    settings: Optional[InsightSettings] = None,
    *,
    nickname: Optional[str] = None,
    dry_run: bool = False,
    sync_yaml: bool = True,
) -> int:
    settings = settings or load_insight_settings()
    if sync_yaml and not dry_run:
        sync_profiles_to_db()

    since_ts = int((datetime.now(TZ_CN) - timedelta(days=HISTORY_BACKFILL_DAYS)).timestamp())
    accounts = insight_repo.list_accounts_for_profiling()

    if nickname:
        accounts = [a for a in accounts if a.get("nickname") == nickname]

    count = 0
    for acc in accounts:
        if not can_profile(
            acc,
            since_ts,
            settings.profile_min_summaries,
            settings.profile_bootstrap_min_titles,
        ):
            continue
        if not needs_profiling(acc, settings.profile_recalibrate_days):
            continue
        try:
            await profile_account(acc["fakeid"], settings, dry_run=dry_run)
            count += 1
        except Exception as e:
            logger.error("画像失败 %s: %s", acc.get("nickname"), e)
    return count


def run_profile(
    *,
    nickname: Optional[str] = None,
    dry_run: bool = False,
    sync_yaml: bool = True,
) -> int:
    return asyncio.run(run_profile_all(nickname=nickname, dry_run=dry_run, sync_yaml=sync_yaml))


def run_sync_yaml_profiles() -> int:
    """仅将 accounts.yaml 画像同步到 DB，不调用 LLM。"""
    return sync_profiles_to_db()
