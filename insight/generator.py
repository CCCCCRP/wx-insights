"""Phase C：洞见报告生成。"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from worker.insight.config import InsightSettings, load_insight_settings
from worker.insight.retriever import (
    format_publish_date,
    format_publish_week,
    format_rag_theme_counts,
    velocity_days,
    velocity_window_note,
)
from worker.insight.llm import structured_completion
from worker.insight.models import ArticleSummaryRecord, PhaseCReportOutput, ThemeClusterOutput
from worker.insight.prompts import PHASE_C_PROMPT
from worker.insight.report_builder import (
    build_account_stats,
    build_report_markdown,
    validate_report_aids,
)

logger = logging.getLogger(__name__)


def _summaries_table(
    summaries: List[ArticleSummaryRecord],
    *,
    max_chars: int | None = None,
) -> str:
    rows = []
    total = 0
    for s in summaries:
        summary_short = s.summary.replace("|", " ").replace("\n", " ")[:120]
        title_short = s.title.replace("|", " ").replace("\n", " ")[:40]
        week = format_publish_week(s.publish_time)
        pub_date = format_publish_date(s.publish_time)
        row = f"| {s.aid} | {week} | {pub_date} | {s.nickname} | {title_short} | {summary_short} |"
        if max_chars is not None and total + len(row) > max_chars:
            rows.append(
                f"| （已截断，共 {len(summaries)} 篇，剩余 {len(summaries) - len(rows)} 篇） | | | | | |"
            )
            break
        rows.append(row)
        total += len(row)
    return "\n".join(rows)


def _themes_json(themes: List[ThemeClusterOutput]) -> str:
    return json.dumps([t.model_dump() for t in themes], ensure_ascii=False, indent=2)


def _summary_index(summaries: List[ArticleSummaryRecord]) -> Dict[str, ArticleSummaryRecord]:
    return {s.aid: s for s in summaries if s.aid}


def _match_cluster(report_theme: str, clusters: List[ThemeClusterOutput]) -> Optional[ThemeClusterOutput]:
    names = {c.theme: c for c in clusters}
    if report_theme in names:
        return names[report_theme]
    for c in clusters:
        if report_theme in c.theme or c.theme in report_theme:
            return c
    return None


def _enrich_theme_lookback(
    report: PhaseCReportOutput,
    clusters: List[ThemeClusterOutput],
    rag_counts: Dict[str, int],
) -> PhaseCReportOutput:
    """按 Phase B velocity_hint + RAG 命中数填充非 LLM 字段。"""
    enriched = []
    for section in report.themes:
        cluster = _match_cluster(section.theme, clusters)
        vel = cluster.velocity_hint if cluster else "medium"
        days = velocity_days(vel)
        rag_n = 0
        if cluster and cluster.theme in rag_counts:
            rag_n = rag_counts[cluster.theme]
        elif section.theme in rag_counts:
            rag_n = rag_counts[section.theme]
        else:
            for name, cnt in rag_counts.items():
                if name in section.theme or section.theme in name:
                    rag_n = max(rag_n, cnt)
        enriched.append(
            section.model_copy(
                update={
                    "velocity_hint": vel,
                    "lookback_days": days,
                    "rag_history_count": rag_n,
                }
            )
        )
    return report.model_copy(update={"themes": enriched})


def _sanitize_history_comparison(
    report: PhaseCReportOutput,
    valid_history_aids: set[str],
) -> PhaseCReportOutput:
    """去掉无效 history_comparison，避免渲染成 ? · ?。"""
    themes = []
    for section in report.themes:
        if section.rag_history_count <= 0:
            themes.append(section.model_copy(update={"history_comparison": []}))
            continue
        cleaned = [
            b
            for b in section.history_comparison
            if (b.past_aid or "").strip() and b.past_aid in valid_history_aids
        ]
        themes.append(section.model_copy(update={"history_comparison": cleaned}))
    return report.model_copy(update={"themes": themes})


async def generate_report(
    *,
    week_id: str,
    themes: List[ThemeClusterOutput],
    summaries: List[ArticleSummaryRecord],
    context_themes_text: str,
    context_articles_text: str = "（暂无历史文章对照）",
    rag_theme_counts: Optional[Dict[str, int]] = None,
    history_index: Optional[Dict[str, ArticleSummaryRecord]] = None,
    settings: Optional[InsightSettings] = None,
) -> tuple[str, dict]:
    settings = settings or load_insight_settings()
    index = _summary_index(summaries)
    hist_index = history_index or {}
    valid_aids = set(index.keys())
    valid_history_aids = set(hist_index.keys())

    reader_focus = "、".join(settings.reader_focus) if settings.reader_focus else "无特别偏好"

    rag_chars_used = len(context_articles_text)
    reserved_other_chars = 8000
    remaining_chars = max(0, settings.phase_c_max_input_tokens - reserved_other_chars - rag_chars_used)
    max_summary_chars = max(4000, remaining_chars)
    table = _summaries_table(summaries, max_chars=max_summary_chars)
    history_table = _summaries_table(
        list(hist_index.values()), max_chars=min(12000, max_summary_chars // 2)
    )
    if len(summaries) > 0 and table.count("\n") < len(summaries) - 1:
        logger.warning(
            "Phase C 摘要表因字符预算限制截断至 %d/%d 篇",
            table.count("\n"),
            len(summaries),
        )

    rag_counts = rag_theme_counts or {}
    prompt = PHASE_C_PROMPT.format(
        reader_focus=reader_focus,
        report_week_id=week_id,
        velocity_window_note=velocity_window_note(),
        rag_theme_counts_note=format_rag_theme_counts(rag_counts),
        context_themes_json=context_themes_text,
        context_articles_text=context_articles_text,
        history_articles_table=history_table or "（暂无）",
        primary_themes_json=_themes_json(themes),
        summaries_table=table,
    )

    structured: PhaseCReportOutput = await structured_completion(
        prompt,
        PhaseCReportOutput,
        model=settings.phase_c_model,
        max_tokens=settings.phase_c_max_tokens,
        max_tokens_ceiling=settings.llm_max_tokens_ceiling,
        settings=settings,
        backend=settings.phase_c_backend,
    )
    structured = _enrich_theme_lookback(structured, themes, rag_counts)
    structured = _sanitize_history_comparison(structured, valid_history_aids)

    aid_warnings = validate_report_aids(structured, valid_aids, valid_history_aids)
    if aid_warnings:
        logger.warning("Phase C aid 校验: %s", "; ".join(aid_warnings[:5]))

    account_stats = build_account_stats(summaries)
    report_md = build_report_markdown(
        structured,
        week_id=week_id,
        index=index,
        history_index=hist_index,
        account_stats=account_stats,
    )

    rag_article_count = context_articles_text.count("\n- ") if context_articles_text else 0
    meta = {
        "model": settings.phase_c_model,
        "llm_backend": settings.phase_c_backend,
        "llm_base_url": (
            settings.cloud_llm_base_url
            if settings.phase_c_backend == "cloud"
            else settings.local_llm_base_url
        ),
        "week_id": week_id,
        "n_primary": len(summaries),
        "n_themes": len(themes),
        "n_themes_report": len(structured.themes),
        "rag_articles_injected": rag_article_count,
        "rag_chars": rag_chars_used,
        "rag_theme_counts": rag_counts,
        "citation_mode": "aid_structured",
        "aid_warnings": aid_warnings,
        "structured_report": structured.model_dump(),
        "summaries_index": [
            {
                "aid": s.aid,
                "fakeid": s.fakeid,
                "nickname": s.nickname,
                "title": s.title,
                "link": s.link,
                "summary": s.summary,
                "publish_time": s.publish_time,
            }
            for s in summaries
        ],
        "history_index": [
            {
                "aid": s.aid,
                "fakeid": s.fakeid,
                "nickname": s.nickname,
                "title": s.title,
                "link": s.link,
                "summary": s.summary,
                "publish_time": s.publish_time,
            }
            for s in hist_index.values()
        ],
    }
    return report_md, meta


def generate_short_report(week_id: str, reason: str) -> str:
    return f"""# 洞见周报 · {week_id}

> {reason}

本周无足够材料生成深度洞见报告。
"""
