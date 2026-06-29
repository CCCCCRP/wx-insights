"""Insight 流水线 Pydantic 数据模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ArticleRecord(BaseModel):
    aid: str
    fakeid: str = ""
    nickname: str = ""
    title: str = ""
    link: str = ""
    publish_time: int = 0
    plain_content: str = ""
    digest: str = ""
    content_fetched: bool = False
    account_lens: str = "general"
    low_confidence: bool = False


class SummaryOutput(BaseModel):
    summary: str
    topic_tags: List[str] = Field(default_factory=list, min_length=1, max_length=5)
    claims: List[str] = Field(default_factory=list, max_length=3)
    sentiment: Literal["neutral", "bullish", "bearish"] = "neutral"
    quality_score: float = Field(ge=0.0, le=1.0)


class ArticleSummaryRecord(BaseModel):
    aid: str
    fakeid: str = ""
    nickname: str = ""
    title: str = ""
    link: str = ""
    publish_time: int = 0
    summary: str
    topic_tags: List[str] = Field(default_factory=list)
    claims: List[Any] = Field(default_factory=list)
    sentiment: str = "neutral"
    quality_score: float = 0.5
    account_lens: str = "general"
    summary_embedding: Optional[List[float]] = None
    model: str = ""
    content_hash: str = ""


class ThemeClusterOutput(BaseModel):
    theme_key: str
    theme: str
    theme_tags: List[str] = Field(default_factory=list)
    aids: List[str] = Field(default_factory=list)
    source_mix: Dict[str, int] = Field(default_factory=dict)
    theme_summary: str
    novelty_hint: str = "延续讨论"
    narrative_chain_id: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    velocity_hint: Literal["fast", "medium", "slow"] = "medium"


class ThemeClusterList(BaseModel):
    themes: List[ThemeClusterOutput]


class AccountProfileOutput(BaseModel):
    fakeid: str
    nickname: str
    insight_lens: Literal["industry", "interview", "science", "business", "general"] = "general"
    insight_tags: List[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)
    sample_aids: List[str] = Field(default_factory=list)
    strip_head_pattern: str = Field(
        default="",
        description=(
            "正文开头需要整段去除的固定模板文字（逐字匹配前缀，非正则）；"
            "若正文无固定开头模板则留空字符串"
        ),
    )
    strip_tail_markers: List[str] = Field(
        default_factory=list,
        description=(
            "结尾截断标记列表；遇到列表中任意字符串即截断其后所有内容；"
            "若无固定结尾模板则留空列表"
        ),
    )


class SelectionStats(BaseModel):
    week_id: str
    primary_count: int = 0
    primary_with_content: int = 0
    context_summary_count: int = 0
    lens_distribution: Dict[str, int] = Field(default_factory=dict)
    source: str = "db"


class CitedBullet(BaseModel):
    """一条可溯源陈述；link 由代码从 aid 注入，LLM 只填 aid。"""
    statement: str = Field(description="一条陈述，不含 URL")
    aid: str = Field(description="溯源 aid，必须来自本周摘要清单")


class HistoryComparisonBullet(BaseModel):
    """历史 vs 本周对比：两侧各一条陈述 + 各一个 aid（代码注入 link）。"""
    past_part: str = Field(description="过去侧事实/判断，不含 URL")
    past_aid: str = Field(description="过去侧 aid，必须来自 RAG 历史文章清单")
    this_week_part: str = Field(description="本周侧事实/判断，不含 URL")
    aid: str = Field(description="本周侧 aid，必须来自本周摘要清单")


class ThemeSectionReport(BaseModel):
    theme: str = Field(description="主题名 4-10 字")
    theme_tags: List[str] = Field(default_factory=list, max_length=6)
    brief_summary: str = Field(description="一句话总结，20-40 字，不含 URL")
    details: List[CitedBullet] = Field(
        min_length=2, max_length=8,
        description="详细概括，每条一个事实/洞见，必须有 aid",
    )
    history_comparison: List[HistoryComparisonBullet] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "历史对比：past_aid 来自 RAG 历史清单，aid 来自本周清单；"
            "RAG 命中为 0 时必须为空数组"
        ),
    )
    velocity_hint: Literal["fast", "medium", "slow"] = Field(
        default="medium",
        description="代码填充：短/中/长周期，非 LLM 填写",
    )
    lookback_days: int = Field(
        default=180,
        description="代码填充：RAG 回溯天数，非 LLM 填写",
    )
    rag_history_count: int = Field(
        default=0,
        description="代码填充：该主题 RAG 命中历史文章数，非 LLM 填写",
    )
    insights: List[str] = Field(
        min_length=2, max_length=4,
        description=(
            "启示与展望：结合历史趋势和本周信号，给出 2-4 条前瞻性判断。"
            "可引用 Context Mirror 中的频次数据（如：6个月前每周仅1篇 → 本周5篇）说明加速趋势；"
            "指出读者应重点关注的方向或风险。纯文字，不含 URL，不含 aid。"
        ),
    )


class PhaseCReportOutput(BaseModel):
    themes: List[ThemeSectionReport]
    follow_ups: List[str] = Field(min_length=2, max_length=5)
