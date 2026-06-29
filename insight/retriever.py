"""Context 检索：pgvector 语义搜索 themes / 历史文章（整篇 Top-K RAG）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence

from worker.db import insight_repo
from worker.db.connection import database_available
from worker.insight.models import ThemeClusterOutput

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

# 动态 Context 窗口（§5.3）
VELOCITY_DAYS = {"fast": 60, "medium": 180, "slow": 365}

CYCLE_LABELS: Dict[str, str] = {
    "fast": "短周期",
    "medium": "中周期",
    "slow": "长周期",
}

CYCLE_HINTS: Dict[str, str] = {
    "fast": "如 AI 产业、融资、产品发布",
    "medium": "如商业趋势、应用落地",
    "slow": "如基础科学、数学教育、政策监管",
}


def velocity_days(velocity_hint: str) -> int:
    return VELOCITY_DAYS.get(velocity_hint, VELOCITY_DAYS["medium"])


def lookback_section_title(
    lookback_days: int,
    velocity_hint: str = "medium",
    *,
    rag_history_count: int = -1,
) -> str:
    """生成 ③ 区块标题：具体天数 + 长/中/短周期标注。"""
    cycle = CYCLE_LABELS.get(velocity_hint, "中周期")
    hint = CYCLE_HINTS.get(velocity_hint, "")
    if hint:
        title = f"历史对比 · 回溯 {lookback_days} 天 · {cycle}（{hint}）"
    else:
        title = f"历史对比 · 回溯 {lookback_days} 天 · {cycle}"
    if rag_history_count >= 0:
        title += f" · RAG {rag_history_count} 篇"
    return title


def velocity_window_note() -> str:
    """供 Phase C Prompt 注入的回溯窗口说明。"""
    lines = [
        "各主题对比窗口由 Phase B 的 velocity_hint 决定（见主题簇 JSON）：",
    ]
    for hint in ("fast", "medium", "slow"):
        days = VELOCITY_DAYS[hint]
        cycle = CYCLE_LABELS[hint]
        example = CYCLE_HINTS[hint]
        lines.append(f"- {hint} / {cycle}：回溯 {days} 天（{example}）")
    lines.append("Context Mirror 时间线最多展示近 4 个 week_id 节点。")
    return "\n".join(lines)


def format_rag_theme_counts(counts: Dict[str, int]) -> str:
    """各主题 RAG 命中篇数，供 LLM 决定 history_comparison 写几条。"""
    if not counts:
        return "（暂无历史文章，history_comparison 每个主题 1-2 条即可，并注明历史不足）"
    lines = []
    for theme, n in sorted(counts.items(), key=lambda x: -x[1]):
        if n >= 3:
            suggest = "建议 2-3 条对比"
        elif n >= 2:
            suggest = "建议 2 条对比"
        elif n == 1:
            suggest = "建议 1 条对比"
        else:
            suggest = "可写 1 条或注明新话题"
        lines.append(f"- {theme}：检索到 {n} 篇历史文章 → {suggest}")
    return "\n".join(lines)


def publish_time_window(week_start_ts: int, velocity_hint: str) -> tuple[int, int]:
    """历史 Context 时间窗：[week_start - velocity_days, week_start - 1]。"""
    days = velocity_days(velocity_hint)
    return week_start_ts - days * 86400, week_start_ts - 1


RagEmbeddingMode = str  # hybrid | summary | content | auto


def fetch_similar_summaries_for_embedding(
    embedding: List[float],
    publish_time_min: int,
    publish_time_max: int,
    *,
    limit: int = 5,
    exclude_aids: Optional[Sequence[str]] = None,
    min_similarity: float = 0.5,
    embedding_mode: RagEmbeddingMode = "hybrid",
    content_min_similarity: float = 0.50,
) -> List[Dict[str, Any]]:
    """检索与 embedding 最相似的历史文章。

    hybrid（默认）：summary_embedding + content_embedding 双路合并（按 aid 取最高 similarity）。
    auto：先 summary，无结果再 content。
    历史库若仅有 content_embedding（未跑 Phase A），content 路保证仍有召回。
    """
    if not database_available():
        return []

    exclude = list(exclude_aids or ())
    pool: Dict[str, Dict[str, Any]] = {}

    def _ingest(rows: List[Dict[str, Any]], space: str) -> None:
        for row in rows:
            aid = row.get("aid") or ""
            if not aid:
                continue
            tagged = dict(row)
            tagged["_embedding_space"] = space
            sim = float(tagged.get("similarity") or 0)
            prev = pool.get(aid)
            if prev is None or sim > float(prev.get("similarity") or 0):
                pool[aid] = tagged

    try:
        use_summary = embedding_mode in ("summary", "hybrid", "auto")
        use_content = embedding_mode in ("content", "hybrid")
        if use_summary:
            summary_rows = insight_repo.fetch_similar_summaries_for_embedding(
                embedding,
                publish_time_min,
                publish_time_max,
                limit=limit,
                exclude_aids=exclude,
                min_similarity=min_similarity,
            )
            _ingest(summary_rows, "summary")
        if use_content or (embedding_mode == "auto" and not pool):
            content_rows = insight_repo.fetch_similar_by_content_embedding(
                embedding,
                publish_time_min,
                publish_time_max,
                limit=limit,
                exclude_aids=exclude,
                min_similarity=content_min_similarity,
            )
            _ingest(content_rows, "content")

        merged = sorted(pool.values(), key=lambda r: float(r.get("similarity") or 0), reverse=True)
        return merged[:limit]
    except Exception as e:
        logger.warning("篇级 RAG 检索失败: %s", e)
        return []


def format_publish_week(publish_time: int) -> str:
    """ISO 周次，如 2026-W25。"""
    if not publish_time:
        return "?"
    return datetime.fromtimestamp(publish_time, TZ_CN).strftime("%G-W%V")


def format_publish_date(publish_time: int) -> str:
    """发布日期 YYYY-MM-DD（北京时间）。"""
    if not publish_time:
        return "?"
    return datetime.fromtimestamp(publish_time, TZ_CN).strftime("%Y-%m-%d")


def week_dates_label(week_id: str) -> str:
    """ISO 周 → 起止日期，如 2026-04-14 ~ 2026-04-20。"""
    from worker.crawl.week import week_range

    try:
        start_ts, end_ts, _ = week_range(week_id)
        return f"{format_publish_date(start_ts)} ~ {format_publish_date(end_ts)}"
    except ValueError:
        return week_id


def dates_label_for_publish(publish_time: int) -> str:
    """文章发布日；有 publish_time 时优先用具体日期，否则退回周区间。"""
    if not publish_time:
        return "?"
    return format_publish_date(publish_time)


def _format_publish_week(publish_time: int) -> str:
    return format_publish_week(publish_time)


def excerpt_for_rag(
    row: Dict[str, Any],
    *,
    max_chars: int = 300,
) -> str:
    """优先用 Phase A 摘要；无摘要则取正文开头片段。"""
    summary = (row.get("summary") or "").strip()
    if summary:
        return summary[:max_chars]
    plain = (row.get("plain_content") or "").strip().replace("\n", " ")
    if plain:
        return plain[:max_chars]
    return ""


def format_context_themes(themes: List[Dict[str, Any]]) -> str:
    """供 Phase C prompt 使用的 Context Mirror 文本。"""
    if not themes:
        return "（暂无历史主题对照）"
    lines = []
    for t in themes:
        timeline = t.get("timeline") or []
        if isinstance(timeline, str):
            timeline = json.loads(timeline)
        if not timeline:
            continue
        parts = []
        for entry in timeline[-4:]:
            parts.append(f"{entry.get('week_id', '?')}:{entry.get('status', '?')}({entry.get('article_count', 0)}篇)")
        arc = " → ".join(parts)
        # velocity 优先取 DB 字段，回退到检索时附带的主题 velocity_hint
        vel = t.get("velocity") or t.get("_velocity_hint") or "medium"
        lines.append(f"- {t.get('display_name', '?')} [{vel}]: {arc}")
    return "\n".join(lines) if lines else "（暂无历史主题对照）"


def format_rag_articles(
    rows: List[Dict[str, Any]],
    *,
    excerpt_chars: int = 300,
) -> str:
    """格式化篇级 RAG 检索结果供 Phase C 使用。"""
    if not rows:
        return "（暂无历史文章对照）"
    lines = []
    for row in rows:
        aid = row.get("aid") or "?"
        title = (row.get("title") or "?").replace("\n", " ")
        nickname = row.get("nickname") or "?"
        pub = int(row.get("publish_time") or 0)
        week = _format_publish_week(pub)
        pub_date = format_publish_date(pub)
        theme = row.get("_for_theme") or ""
        prefix = f"[{theme}] " if theme else ""
        excerpt = excerpt_for_rag(row, max_chars=excerpt_chars)
        if not excerpt:
            continue
        lines.append(
            f"- {prefix}aid:{aid} | {title} · {nickname} · {week} ({pub_date})\n  {excerpt}"
        )
    return "\n".join(lines) if lines else "（暂无历史文章对照）"


def rag_row_to_summary(row: Dict[str, Any]) -> "ArticleSummaryRecord":
    from worker.insight.models import ArticleSummaryRecord

    excerpt = excerpt_for_rag(row, max_chars=500)
    return ArticleSummaryRecord(
        aid=row.get("aid") or "",
        fakeid="",
        nickname=row.get("nickname") or "",
        title=row.get("title") or "",
        link=row.get("link") or "",
        publish_time=int(row.get("publish_time") or 0),
        summary=row.get("summary") or excerpt,
        quality_score=0.5,
    )


def _normalize_topic_tags(tags: Any) -> set[str]:
    if not tags:
        return set()
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = [tags]
    return {str(t).strip().lower() for t in tags if t and str(t).strip()}


def topic_tags_overlap(theme_tags: Sequence[str], row_tags: Any) -> bool:
    """历史文 topic_tags 与主题 theme_tags 至少有一个交集。

    历史文无 topic_tags 时放行（依赖向量相似度；常见于未跑 Phase A 的正文库）。
    仅当双方都有标签且无交集时才剔除。
    """
    theme_norm = _normalize_topic_tags(theme_tags)
    if not theme_norm:
        return True
    row_norm = _normalize_topic_tags(row_tags)
    if not row_norm:
        return True
    return bool(theme_norm & row_norm)


def _merge_rag_candidate(
    pool: Dict[str, Dict[str, Any]],
    row: Dict[str, Any],
    source: str,
) -> None:
    aid = row.get("aid") or ""
    if not aid:
        return
    sim = float(row.get("similarity") or 0)
    existing = pool.get(aid)
    if existing is None or sim > float(existing.get("similarity") or 0):
        merged = dict(row)
        merged["_rag_source"] = source
        pool[aid] = merged


def _collect_theme_rag_candidates(
    theme: ThemeClusterOutput,
    *,
    centroid: Optional[List[float]],
    primary_by_aid: Dict[str, "ArticleSummaryRecord"],
    pub_min: int,
    pub_max: int,
    exclude_aids: Sequence[str],
    per_theme_limit: int,
    per_article_limit: int,
    min_similarity: float,
    embedding_mode: str = "hybrid",
    content_min_similarity: float = 0.50,
) -> Dict[str, Dict[str, Any]]:
    """主题内合并 centroid 检索 + 逐篇 Primary 检索，按 aid 保留最高 similarity。"""
    pool: Dict[str, Dict[str, Any]] = {}
    fetch_kw = {
        "exclude_aids": exclude_aids,
        "min_similarity": min_similarity,
        "embedding_mode": embedding_mode,
        "content_min_similarity": content_min_similarity,
    }

    if centroid:
        for row in fetch_similar_summaries_for_embedding(
            centroid,
            pub_min,
            pub_max,
            limit=per_theme_limit,
            **fetch_kw,
        ):
            space = row.get("_embedding_space") or "unknown"
            _merge_rag_candidate(pool, row, f"centroid:{space}")

    for aid in theme.aids:
        rec = primary_by_aid.get(aid)
        if not rec or not rec.summary_embedding:
            continue
        for row in fetch_similar_summaries_for_embedding(
            rec.summary_embedding,
            pub_min,
            pub_max,
            limit=per_article_limit,
            **fetch_kw,
        ):
            space = row.get("_embedding_space") or "unknown"
            _merge_rag_candidate(pool, row, f"primary:{aid}:{space}")

    return pool


def _filter_theme_rag_rows(
    pool: Dict[str, Dict[str, Any]],
    theme: ThemeClusterOutput,
    *,
    tag_filter: bool,
    per_theme_limit: int,
) -> tuple[List[Dict[str, Any]], int]:
    """标签过滤 + 按相似度截断；返回 (保留行, 被标签过滤掉的篇数)。"""
    kept: List[Dict[str, Any]] = []
    tag_rejected = 0
    for row in sorted(pool.values(), key=lambda r: float(r.get("similarity") or 0), reverse=True):
        if tag_filter and not topic_tags_overlap(theme.theme_tags, row.get("topic_tags")):
            tag_rejected += 1
            continue
        kept.append(row)
        if len(kept) >= per_theme_limit:
            break
    return kept, tag_rejected


def get_context_for_themes(
    themes: List[ThemeClusterOutput],
    centroids: Dict[str, List[float]],
    week_id: str,
    limit: int = 10,
) -> str:
    """对每个 Primary 主题 centroid 检索历史主题；按 velocity_hint 动态调整回溯窗口。"""
    if not database_available() or not centroids:
        return "（暂无历史主题对照）"

    seen: set[str] = set()
    all_rows: List[Dict[str, Any]] = []

    for theme in themes:
        embedding = centroids.get(theme.theme_key)
        if not embedding:
            continue
        days = velocity_days(theme.velocity_hint)
        cutoff = insight_repo.week_id_minus_days(week_id, days)
        try:
            rows = insight_repo.fetch_context_themes_for_embedding(
                embedding, cutoff, limit=3,
            )
            for row in rows:
                name = row.get("display_name", "")
                if name and name not in seen:
                    seen.add(name)
                    row["_velocity_hint"] = theme.velocity_hint
                    all_rows.append(row)
        except Exception as e:
            logger.warning("Context 检索失败 theme=%s: %s", theme.theme_key, e)

    all_rows.sort(key=lambda r: float(r.get("similarity") or 0), reverse=True)
    return format_context_themes(all_rows[:limit])


def get_rag_context_for_themes(
    themes: List[ThemeClusterOutput],
    centroids: Dict[str, List[float]],
    week_start_ts: int,
    primary_aids: Sequence[str],
    *,
    primary_summaries: Optional[Sequence["ArticleSummaryRecord"]] = None,
    per_theme_limit: int = 4,
    per_article_limit: int = 2,
    total_limit: int = 30,
    excerpt_chars: int = 300,
    min_similarity: float = 0.58,
    content_min_similarity: float = 0.50,
    embedding_mode: str = "hybrid",
    tag_filter: bool = True,
) -> tuple[str, Dict[str, int], Dict[str, "ArticleSummaryRecord"], List[Dict[str, Any]]]:
    """篇级 RAG：summary_embedding 同空间检索 + 逐篇 Primary + 标签过滤。

    每个主题：centroid Top-K 与 theme 内各 Primary 摘要 Top-K 合并去重，
    再按 theme_tags 过滤，取 per_theme_limit 篇；全局按 similarity 截断 total_limit。

    返回 (格式化文本, 各主题命中篇数, aid→历史文章索引, 检索日志供 meta)。
    """
    from worker.insight.models import ArticleSummaryRecord

    if not database_available() or not centroids:
        return "（暂无历史文章对照）", {}, {}, []

    exclude = set(primary_aids)
    primary_by_aid: Dict[str, ArticleSummaryRecord] = {
        s.aid: s for s in (primary_summaries or []) if s.aid
    }
    seen_aids: set[str] = set()
    all_rows: List[Dict[str, Any]] = []
    per_theme_counts: Dict[str, int] = {}
    rag_log: List[Dict[str, Any]] = []

    for theme in themes:
        embedding = centroids.get(theme.theme_key)
        if not embedding and not any(primary_by_aid.get(a) for a in theme.aids):
            per_theme_counts[theme.theme] = 0
            continue

        pub_min, pub_max = publish_time_window(week_start_ts, theme.velocity_hint)
        pool = _collect_theme_rag_candidates(
            theme,
            centroid=embedding,
            primary_by_aid=primary_by_aid,
            pub_min=pub_min,
            pub_max=pub_max,
            exclude_aids=list(exclude),
            per_theme_limit=per_theme_limit,
            per_article_limit=per_article_limit,
            min_similarity=min_similarity,
            embedding_mode=embedding_mode,
            content_min_similarity=content_min_similarity,
        )
        theme_rows, tag_rejected = _filter_theme_rag_rows(
            pool,
            theme,
            tag_filter=tag_filter,
            per_theme_limit=per_theme_limit,
        )

        if tag_rejected:
            logger.debug(
                "RAG 标签过滤 theme=%s 剔除 %d 篇（theme_tags=%s）",
                theme.theme,
                tag_rejected,
                theme.theme_tags[:4],
            )

        theme_n = 0
        for row in theme_rows:
            aid = row.get("aid") or ""
            if not aid or aid in seen_aids:
                continue
            seen_aids.add(aid)
            row["_for_theme"] = theme.theme
            all_rows.append(row)
            theme_n += 1
            rag_log.append({
                "aid": aid,
                "theme": theme.theme,
                "similarity": round(float(row.get("similarity") or 0), 4),
                "source": row.get("_rag_source") or "unknown",
                "embedding_space": row.get("_embedding_space") or "unknown",
                "title": row.get("title") or "",
                "topic_tags": list(row.get("topic_tags") or []),
            })
        per_theme_counts[theme.theme] = theme_n

    all_rows.sort(key=lambda r: float(r.get("similarity") or 0), reverse=True)
    n_retrieved = len(all_rows)
    injected = all_rows[:total_limit]
    injected_aids = {r.get("aid") for r in injected}
    rag_log = [e for e in rag_log if e.get("aid") in injected_aids]

    result = format_rag_articles(injected, excerpt_chars=excerpt_chars)
    hist_index: Dict[str, ArticleSummaryRecord] = {}
    for row in injected:
        aid = row.get("aid") or ""
        if aid:
            hist_index[aid] = rag_row_to_summary(row)
    if n_retrieved > total_limit:
        logger.debug("RAG 检索 %d 篇，截断为 %d 篇注入 Prompt", n_retrieved, total_limit)
    n_content = sum(1 for r in injected if r.get("_embedding_space") == "content")
    n_summary = sum(1 for r in injected if r.get("_embedding_space") == "summary")
    logger.info(
        "RAG 完成: 主题=%d 注入=%d 篇 (summary=%d content=%d) mode=%s "
        "sim≥%.2f/%.2f tag_filter=%s",
        len(themes),
        len(injected),
        n_summary,
        n_content,
        embedding_mode,
        min_similarity,
        content_min_similarity,
        tag_filter,
    )
    return result, per_theme_counts, hist_index, rag_log
