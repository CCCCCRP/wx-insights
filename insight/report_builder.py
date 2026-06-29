"""Phase C 结构化输出 → Markdown + HTML 数据。

link 完全由 aid 映射注入，LLM 不写任何 URL。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from worker.insight.models import (
    ArticleSummaryRecord,
    CitedBullet,
    HistoryComparisonBullet,
    PhaseCReportOutput,
)
from worker.insight.retriever import (
    dates_label_for_publish,
    lookback_section_title,
)

logger = logging.getLogger(__name__)

AccountStats = Dict[str, List[ArticleSummaryRecord]]  # nickname → articles


def build_account_stats(summaries: List[ArticleSummaryRecord]) -> AccountStats:
    """按公众号分组，保留文章列表（有 link 的优先）。"""
    stats: AccountStats = defaultdict(list)
    for s in summaries:
        stats[s.nickname].append(s)
    return dict(stats)


def _short_title(title: str, max_len: int = 18) -> str:
    t = (title or "?").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def cite_link(
    aid: str,
    index: Dict[str, ArticleSummaryRecord],
) -> Tuple[str, str, str]:
    """返回 (title, link, nickname)，aid 无效时返回占位。"""
    if not (aid or "").strip():
        return "?", "", "?"
    rec = index.get(aid)
    if not rec:
        logger.warning("未知 aid: %s", aid)
        return "?", "", "?"
    return rec.title or "?", rec.link or "", rec.nickname or "?"


def inline_cite_md(
    aid: str,
    index: Dict[str, ArticleSummaryRecord],
    *,
    prefix: str = "",
) -> str:
    rec = index.get(aid)
    title, link, _ = cite_link(aid, index)
    pub = dates_label_for_publish(rec.publish_time) if rec else "?"
    label = _short_title(title)
    tag = f"{prefix}{pub} · {label}" if prefix else f"{pub} · {label}"
    if link:
        return f"[{tag}]({link})"
    return tag


def bullet_md(b: CitedBullet, index: Dict[str, ArticleSummaryRecord]) -> str:
    return f"- {b.statement.rstrip('。')}。{inline_cite_md(b.aid, index)}"


def history_bullet_md(
    b: HistoryComparisonBullet,
    week_index: Dict[str, ArticleSummaryRecord],
    history_index: Dict[str, ArticleSummaryRecord],
) -> str:
    past_rec = history_index.get(b.past_aid)
    now_rec = week_index.get(b.aid)
    past_dates = dates_label_for_publish(past_rec.publish_time) if past_rec else "?"
    now_dates = dates_label_for_publish(now_rec.publish_time) if now_rec else "?"
    past_text = b.past_part.rstrip("。")
    now_text = b.this_week_part.rstrip("。")
    past_cite = inline_cite_md(b.past_aid, history_index)
    now_cite = inline_cite_md(b.aid, week_index)
    return (
        f"- 过去（{past_dates}）：{past_text}。{past_cite}；"
        f"本周（{now_dates}）：{now_text}。{now_cite}"
    )


def build_report_markdown(
    report: PhaseCReportOutput,
    *,
    week_id: str,
    index: Dict[str, ArticleSummaryRecord],
    history_index: Dict[str, ArticleSummaryRecord],
    account_stats: AccountStats,
) -> str:
    parts: List[str] = [f"# 洞见周报 · {week_id}", ""]

    parts.append("## 本周更新来源")
    parts.append("")
    for nickname, arts in sorted(account_stats.items(), key=lambda x: -len(x[1])):
        parts.append(f"**{nickname}**（{len(arts)} 篇）")
        for a in arts:
            if a.link:
                parts.append(f"  - [{_short_title(a.title, 30)}]({a.link})")
            else:
                parts.append(f"  - {_short_title(a.title, 30)}")
    parts.append("")

    parts.append("## 分类洞见")
    parts.append("")
    for theme in report.themes:
        tags = " ".join(f"#{t}" for t in theme.theme_tags[:6])
        parts.append(f"### {theme.theme}（{tags}）")
        parts.append("")
        parts.append(f"**总概括**：{theme.brief_summary}")
        parts.append("")
        parts.append("**详细概括**")
        for b in theme.details:
            parts.append(bullet_md(b, index))
        parts.append("")
        if theme.history_comparison:
            hist_title = lookback_section_title(
                theme.lookback_days, theme.velocity_hint,
                rag_history_count=theme.rag_history_count,
            )
            parts.append(f"**{hist_title}**")
            for b in theme.history_comparison:
                parts.append(history_bullet_md(b, index, history_index))
            parts.append("")
        elif theme.rag_history_count <= 0:
            parts.append("**历史对比**")
            parts.append("")
            parts.append("本主题本周首次出现，暂无历史对比数据。")
            parts.append("")
        if theme.insights:
            parts.append("**启示与展望**")
            for item in theme.insights:
                parts.append(f"- {item.strip()}")
        parts.append("")

    parts.append("## 值得跟进")
    for i, item in enumerate(report.follow_ups, start=1):
        parts.append(f"{i}. {item.strip()}")

    return "\n".join(parts).strip() + "\n"


def validate_report_aids(
    report: PhaseCReportOutput,
    valid_aids: set[str],
    valid_history_aids: set[str] | None = None,
) -> List[str]:
    warnings: List[str] = []
    hist_aids = valid_history_aids or set()

    def _check(aid: str, ctx: str, pool: set[str]) -> None:
        if aid and aid not in pool:
            warnings.append(f"无效 aid [{aid}] @ {ctx}")

    for t in report.themes:
        for j, b in enumerate(t.details):
            _check(b.aid, f"{t.theme}/details#{j + 1}", valid_aids)
        for j, b in enumerate(t.history_comparison):
            _check(b.past_aid, f"{t.theme}/history#{j + 1}/past", hist_aids)
            _check(b.aid, f"{t.theme}/history#{j + 1}/this_week", valid_aids)

    return warnings
