"""Rolling Themes 更新（themes 表）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from worker.db import insight_repo
from worker.db.connection import database_available
from worker.insight.models import ThemeClusterOutput

logger = logging.getLogger(__name__)


def compact_rolling_themes(themes: List[Dict[str, Any]], limit: int = 20) -> str:
    """供 Phase B prompt 使用的紧凑格式。"""
    if not themes:
        return "（暂无历史主题）"
    lines = []
    for t in themes[:limit]:
        timeline = t.get("timeline") or []
        if isinstance(timeline, str):
            timeline = json.loads(timeline)
        recent = timeline[-3:] if timeline else []
        recent_str = " → ".join(
            f"{e.get('week_id', '?')}({e.get('status', '?')})" for e in recent
        )
        lines.append(f"- {t.get('display_name', '?')} [{', '.join(t.get('theme_tags') or [])}] {recent_str}")
    return "\n".join(lines)


def _timeline_entry(theme: ThemeClusterOutput, week_id: str) -> Dict[str, Any]:
    article_count = len(theme.aids)
    status = "emerging"
    if article_count >= 5:
        status = "mainstream"
    elif article_count >= 2:
        status = "warming"
    return {
        "week_id": week_id,
        "status": status,
        "article_count": article_count,
        "theme_summary": theme.theme_summary[:200],
        "confidence": theme.confidence,
        "novelty_hint": theme.novelty_hint,
    }


def update_rolling_themes(
    clusters: List[ThemeClusterOutput],
    centroids: Dict[str, List[float]],
    week_id: str,
    *,
    similarity_threshold: float = 0.72,
    archive_days: int = 180,
) -> None:
    if not database_available():
        logger.warning("无 DB，跳过 rolling themes 更新")
        return

    for theme in clusters:
        embedding = centroids.get(theme.theme_key)
        if not embedding:
            continue

        entry = _timeline_entry(theme, week_id)
        similar = insight_repo.find_similar_theme(embedding, threshold=similarity_threshold)

        if similar:
            insight_repo.append_theme_timeline(
                similar["id"],
                entry,
                week_id,
                embedding,
                display_name=theme.theme,
                theme_tags=theme.theme_tags,
            )
            logger.info("更新主题 timeline: %s → %s", similar["theme_key"], week_id)
        else:
            by_key = insight_repo.fetch_theme_by_key(theme.theme_key)
            if by_key:
                if by_key.get("archived"):
                    insight_repo.unarchive_theme(by_key["id"])
                insight_repo.append_theme_timeline(
                    by_key["id"],
                    entry,
                    week_id,
                    embedding,
                    display_name=theme.theme,
                    theme_tags=theme.theme_tags,
                )
                logger.info(
                    "按 theme_key 合并 timeline: %s → %s",
                    theme.theme_key,
                    week_id,
                )
            else:
                insight_repo.insert_theme(
                    theme_key=theme.theme_key,
                    display_name=theme.theme,
                    theme_tags=theme.theme_tags,
                    velocity=theme.velocity_hint,
                    theme_embedding=embedding,
                    timeline_entry=entry,
                    week_id=week_id,
                )
                logger.info("新建主题: %s", theme.theme_key)

    cutoff = insight_repo.week_id_minus_days(week_id, archive_days)
    n = insight_repo.archive_stale_themes(cutoff)
    if n:
        logger.info("归档 %d 个过期主题", n)


def load_active_themes(limit: int = 50) -> List[Dict[str, Any]]:
    if not database_available():
        return []
    try:
        return insight_repo.fetch_active_themes(limit=limit)
    except Exception as e:
        logger.warning("加载 themes 失败: %s", e)
        return []
