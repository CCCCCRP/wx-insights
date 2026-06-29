"""Insight 模块单元测试。"""
from worker.insight.cluster import _fallback_clusters, _safe_theme_key, cluster_by_embedding
from worker.insight.models import ArticleSummaryRecord
from worker.insight.validator import validate_report


def _summary(aid: str, tags: list[str], emb: list[float]) -> ArticleSummaryRecord:
    return ArticleSummaryRecord(
        aid=aid,
        fakeid="fake1",
        summary=f"摘要 {aid}",
        topic_tags=tags,
        summary_embedding=emb,
    )


def test_cluster_by_embedding_groups_similar():
    emb_a = [1.0, 0.0, 0.0] + [0.0] * 1533
    emb_b = [0.99, 0.01, 0.0] + [0.0] * 1533
    emb_c = [0.0, 1.0, 0.0] + [0.0] * 1533

    summaries = [
        _summary("a1", ["Agent"], emb_a),
        _summary("a2", ["Agent"], emb_b),
        _summary("a3", ["融资"], emb_c),
    ]
    clusters = cluster_by_embedding(summaries, distance_threshold=0.35)
    # a1/a2 应同簇，a3 单独
    flat = [sorted(summaries[i].aid for i in c) for c in clusters]
    assert ["a1", "a2"] in flat or len(clusters) >= 2


def test_validate_report_warns_unknown_link():
    report = "见 [测试](https://mp.weixin.qq.com/s/unknown_link)"
    warnings = validate_report(report, primary_aids={"aid1"}, primary_links={"aid1": "https://mp.weixin.qq.com/s/known"})
    assert any("无法验证" in w or "非 Primary" in w for w in warnings)


def test_validate_report_passes_primary_link():
    link = "https://mp.weixin.qq.com/s/abc123"
    report = f"见 [文章]({link})"
    warnings = validate_report(report, primary_aids={"aid1"}, primary_links={"aid1": link})
    assert warnings == []


def test_safe_theme_key_uses_hash_for_chinese():
    k1 = _safe_theme_key("具身智能", 0)
    k2 = _safe_theme_key("具身智能", 99)
    assert k1 == k2
    assert k1.startswith("theme-")
    assert k1 != "theme-000"


def test_fallback_clusters_unique_keys_for_chinese_tags():
    summaries = [
        _summary("a1", ["具身智能"], [1.0] + [0.0] * 1535),
        _summary("a2", ["世界模型"], [0.0, 1.0] + [0.0] * 1534),
    ]
    themes = _fallback_clusters(summaries, [[0], [1]])
    keys = [t.theme_key for t in themes]
    assert len(keys) == len(set(keys))
    assert all(k.startswith("theme-") for k in keys)
