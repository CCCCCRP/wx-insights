"""retriever 单元测试（纯函数 + 集成路径 mock）。"""
from unittest.mock import patch

from worker.insight.models import ArticleSummaryRecord, ThemeClusterOutput
from worker.insight.retriever import (
    VELOCITY_DAYS,
    excerpt_for_rag,
    format_context_themes,
    format_rag_articles,
    get_context_for_themes,
    get_rag_context_for_themes,
    publish_time_window,
    topic_tags_overlap,
    velocity_days,
)


# ── 纯函数 ────────────────────────────────────────────────

def test_velocity_days_mapping():
    assert velocity_days("fast") == 60
    assert velocity_days("medium") == 180
    assert velocity_days("slow") == 365
    assert velocity_days("unknown") == VELOCITY_DAYS["medium"]


def test_publish_time_window():
    week_start = 1_700_000_000
    pub_min, pub_max = publish_time_window(week_start, "fast")
    assert pub_max == week_start - 1
    assert pub_min == week_start - 60 * 86400


def test_topic_tags_overlap():
    assert topic_tags_overlap(["AI", "Agent"], ["agent", "工具"]) is True
    assert topic_tags_overlap(["心理学"], ["数学", "教育"]) is False
    assert topic_tags_overlap([], ["任意"]) is True
    assert topic_tags_overlap(["AI"], []) is True


def test_excerpt_prefers_summary():
    row = {"summary": "这是摘要内容", "plain_content": "正文很长" * 100}
    assert excerpt_for_rag(row, max_chars=50) == "这是摘要内容"


def test_excerpt_falls_back_to_plain_content():
    row = {"summary": "", "plain_content": "正文开头部分" + "x" * 500}
    excerpt = excerpt_for_rag(row, max_chars=20)
    assert excerpt.startswith("正文开头部分")
    assert len(excerpt) <= 20


def test_excerpt_empty_when_no_content():
    assert excerpt_for_rag({"summary": None, "plain_content": None}) == ""


def test_format_rag_articles_includes_theme_and_excerpt():
    rows = [
        {
            "title": "测试文章",
            "nickname": "测试号",
            "publish_time": 1_700_000_000,
            "similarity": 0.85,
            "summary": "相关摘要",
            "_for_theme": "Agent 工具",
        }
    ]
    text = format_rag_articles(rows)
    assert "Agent 工具" in text
    assert "测试文章" in text
    assert "相关摘要" in text
    assert "sim=" not in text
    assert "0.85" not in text


def test_format_rag_articles_empty():
    assert format_rag_articles([]) == "（暂无历史文章对照）"


def test_format_rag_articles_skips_no_excerpt():
    rows = [{"title": "空文章", "nickname": "?", "publish_time": 0, "summary": None, "plain_content": None}]
    assert format_rag_articles(rows) == "（暂无历史文章对照）"


def test_format_context_themes_velocity_hint_fallback():
    """_velocity_hint 应在 DB velocity 字段缺失时作为 fallback 展示。"""
    themes = [
        {
            "display_name": "Agent 工具链",
            "velocity": None,
            "_velocity_hint": "fast",
            "timeline": [{"week_id": "2026-W20", "status": "active", "article_count": 3}],
            "similarity": 0.9,
        }
    ]
    text = format_context_themes(themes)
    assert "Agent 工具链" in text
    assert "[fast]" in text


# ── get_rag_context_for_themes mock 路径 ─────────────────

def _make_theme(key: str, velocity: str = "medium") -> ThemeClusterOutput:
    return ThemeClusterOutput(
        theme_key=key,
        theme=f"主题·{key}",
        velocity_hint=velocity,
        theme_summary="summary",
    )


@patch("worker.insight.retriever.database_available", return_value=True)
@patch("worker.insight.retriever.fetch_similar_summaries_for_embedding")
def test_rag_deduplication(mock_fetch, _mock_db):
    """同一篇文章被多个主题检索到时只注入一次。"""
    shared_article = {
        "aid": "a1", "title": "共享文章", "nickname": "测试号",
        "publish_time": 1_700_000_000, "summary": "摘要内容", "similarity": 0.8,
        "topic_tags": ["AI"],
    }
    mock_fetch.return_value = [shared_article]

    themes = [_make_theme("t1"), _make_theme("t2")]
    centroids = {"t1": [0.1] * 1024, "t2": [0.2] * 1024}

    result, counts, hist_index, rag_log = get_rag_context_for_themes(
        themes, centroids, week_start_ts=1_700_000_000,
        primary_aids=[], per_theme_limit=5, total_limit=10, tag_filter=False,
    )

    assert result.count("共享文章") == 1
    assert sum(counts.values()) == 1
    assert "a1" in hist_index
    assert len(rag_log) == 1


@patch("worker.insight.retriever.database_available", return_value=True)
@patch("worker.insight.retriever.fetch_similar_summaries_for_embedding")
def test_rag_excludes_primary_aids(mock_fetch, _mock_db):
    """primary_aids 中的文章不应出现在 RAG 结果中。"""
    mock_fetch.return_value = []

    themes = [_make_theme("t1")]
    centroids = {"t1": [0.1] * 1024}
    primary_aids = ["a1", "a2"]

    get_rag_context_for_themes(
        themes, centroids, week_start_ts=1_700_000_000,
        primary_aids=primary_aids, per_theme_limit=3,
    )

    call_kwargs = mock_fetch.call_args
    passed_exclude = call_kwargs.kwargs.get("exclude_aids") or call_kwargs[1].get("exclude_aids") or []
    assert "a1" in passed_exclude
    assert "a2" in passed_exclude


@patch("worker.insight.retriever.database_available", return_value=False)
def test_rag_returns_placeholder_when_no_db(_mock_db):
    themes = [_make_theme("t1")]
    centroids = {"t1": [0.1] * 1024}
    text, counts, hist_index, rag_log = get_rag_context_for_themes(themes, centroids, 1_700_000_000, [])
    assert text == "（暂无历史文章对照）"
    assert counts == {}
    assert hist_index == {}
    assert rag_log == []


@patch("worker.insight.retriever.database_available", return_value=True)
@patch("worker.insight.retriever.fetch_similar_summaries_for_embedding")
def test_rag_per_article_and_tag_filter(mock_fetch, _mock_db):
    """逐篇 Primary 检索 + 标签过滤：无标签交集的历史文应被剔除。"""
    mock_fetch.side_effect = [
        [{"aid": "h1", "title": "相关", "nickname": "号", "publish_time": 1_700_000_000,
          "summary": "摘要", "similarity": 0.9, "topic_tags": ["AI", "Agent"]}],
        [{"aid": "h2", "title": "无关", "nickname": "号", "publish_time": 1_700_000_000,
          "summary": "摘要", "similarity": 0.95, "topic_tags": ["端午", "习俗"]}],
    ]

    theme = ThemeClusterOutput(
        theme_key="t1",
        theme="Agent 工具",
        theme_tags=["AI", "Agent"],
        aids=["p1"],
        theme_summary="summary",
    )
    primary = ArticleSummaryRecord(
        aid="p1", summary="本周", summary_embedding=[0.2] * 1024,
    )

    text, counts, hist_index, rag_log = get_rag_context_for_themes(
        [theme],
        {"t1": [0.1] * 1024},
        week_start_ts=1_700_000_000,
        primary_aids=["p1"],
        primary_summaries=[primary],
        per_theme_limit=4,
        per_article_limit=2,
        tag_filter=True,
    )

    assert counts["Agent 工具"] == 1
    assert "h1" in hist_index
    assert "h2" not in hist_index
    assert "相关" in text
    assert "无关" not in text
    assert rag_log[0]["source"].startswith("centroid")


@patch("worker.insight.retriever.database_available", return_value=True)
@patch("worker.insight.retriever.insight_repo.fetch_similar_by_content_embedding")
@patch("worker.insight.retriever.insight_repo.fetch_similar_summaries_for_embedding")
def test_rag_hybrid_falls_back_to_content(mock_summary, mock_content, _mock_db):
    """summary 路无历史向量时，hybrid 仍从 content_embedding 召回。"""
    mock_summary.return_value = []
    mock_content.return_value = [{
        "aid": "h99", "title": "正文召回", "nickname": "号",
        "publish_time": 1_700_000_000, "summary": None, "plain_content": "正文",
        "similarity": 0.72, "topic_tags": [],
    }]

    theme = ThemeClusterOutput(
        theme_key="t1", theme="AI 产业", theme_tags=["AI"],
        theme_summary="s",
    )
    text, counts, hist_index, rag_log = get_rag_context_for_themes(
        [theme], {"t1": [0.1] * 1024}, week_start_ts=1_700_000_000,
        primary_aids=[], embedding_mode="hybrid",
        content_min_similarity=0.50, tag_filter=True,
    )
    assert counts["AI 产业"] == 1
    assert "h99" in hist_index
    assert rag_log[0]["embedding_space"] == "content"
    mock_content.assert_called()


@patch("worker.insight.retriever.database_available", return_value=True)
@patch("worker.insight.retriever.insight_repo.fetch_context_themes_for_embedding")
@patch("worker.insight.retriever.insight_repo.week_id_minus_days")
def test_context_themes_velocity_controls_cutoff(mock_minus, mock_fetch, _mock_db):
    """fast 主题应计算 60 天 cutoff，slow 应计算 365 天。"""
    mock_fetch.return_value = []
    mock_minus.return_value = "2026-W01"

    themes = [_make_theme("t_fast", "fast"), _make_theme("t_slow", "slow")]
    centroids = {"t_fast": [0.1] * 1024, "t_slow": [0.2] * 1024}

    get_context_for_themes(themes, centroids, week_id="2026-W25")

    calls = mock_minus.call_args_list
    assert len(calls) == 2
    days_used = {c[0][1] for c in calls}
    assert 60 in days_used
    assert 365 in days_used
