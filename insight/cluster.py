"""Phase B：向量聚类 + LLM 整合。"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from worker.insight.config import InsightSettings, load_insight_settings
from worker.insight.llm import ensure_backend_configured, structured_completion
from worker.insight.models import ArticleSummaryRecord, ThemeClusterList, ThemeClusterOutput
from worker.insight.prompts import PHASE_B_PROMPT

logger = logging.getLogger(__name__)


def cluster_by_embedding(
    summaries: List[ArticleSummaryRecord],
    distance_threshold: float = 0.35,
) -> List[List[int]]:
    """返回簇索引列表（每个簇是 summaries 下标）。"""
    if len(summaries) <= 1:
        return [[i] for i in range(len(summaries))]

    embeddings = []
    valid_indices = []
    for i, s in enumerate(summaries):
        if s.summary_embedding:
            embeddings.append(s.summary_embedding)
            valid_indices.append(i)

    if len(embeddings) <= 1:
        return [[i] for i in range(len(summaries))]

    dist_condensed = pdist(np.array(embeddings), metric="cosine")
    Z = linkage(dist_condensed, method="average")
    labels = fcluster(Z, t=distance_threshold, criterion="distance")

    cluster_map: Dict[int, List[int]] = defaultdict(list)
    for emb_idx, label in enumerate(labels):
        cluster_map[int(label)].append(valid_indices[emb_idx])

    # 无 embedding 的单篇各自成簇
    embedded = set(valid_indices)
    for i in range(len(summaries)):
        if i not in embedded:
            cluster_map[len(cluster_map) + i + 1000] = [i]

    return list(cluster_map.values())


def _cluster_centroid(summaries: List[ArticleSummaryRecord], indices: List[int]) -> Optional[List[float]]:
    vecs = [summaries[i].summary_embedding for i in indices if summaries[i].summary_embedding]
    if not vecs:
        return None
    return np.mean(np.array(vecs), axis=0).tolist()


def _build_candidate_json(
    summaries: List[ArticleSummaryRecord],
    clusters: List[List[int]],
) -> str:
    items = []
    for ci, indices in enumerate(clusters, start=1):
        aids = [summaries[i].aid for i in indices]
        tags: List[str] = []
        for i in indices:
            tags.extend(summaries[i].topic_tags)
        tag_counts = Counter(tags).most_common(6)
        snippets = [summaries[i].summary[:80] for i in indices[:3]]
        items.append({
            "cluster_id": ci,
            "aids": aids,
            "topic_tags": [t for t, _ in tag_counts],
            "snippets": snippets,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def _safe_theme_key(theme_name: str, fallback_idx: int) -> str:
    """生成合法 kebab-case theme_key（兼容中文名称）。"""
    import hashlib
    import re

    ascii_part = re.sub(r"[^\w\s-]", "", theme_name.lower(), flags=re.ASCII)
    key = re.sub(r"[\s_]+", "-", ascii_part).strip("-")[:40]
    if key:
        return key
    digest = hashlib.md5(theme_name.encode("utf-8")).hexdigest()[:10]
    return f"theme-{digest}"


def _fallback_clusters(
    summaries: List[ArticleSummaryRecord],
    clusters: List[List[int]],
) -> List[ThemeClusterOutput]:
    """无 LLM 时的规则兜底。"""
    results: List[ThemeClusterOutput] = []
    seen_keys: set[str] = set()

    for idx, indices in enumerate(clusters):
        if not indices:
            continue
        aids = [summaries[i].aid for i in indices]
        tags: List[str] = []
        lens_mix: Dict[str, int] = {}
        for i in indices:
            tags.extend(summaries[i].topic_tags)
            lens = summaries[i].account_lens
            lens_mix[lens] = lens_mix.get(lens, 0) + 1
        top_tags = [t for t, _ in Counter(tags).most_common(4)]
        theme_name = top_tags[0] if top_tags else "综合快讯"

        raw_key = _safe_theme_key(theme_name, idx)
        # 保证唯一性
        key = raw_key
        suffix = 2
        while key in seen_keys:
            key = f"{raw_key}-{suffix}"
            suffix += 1
        seen_keys.add(key)

        summary_text = "；".join(summaries[i].summary[:60] for i in indices[:3])
        conf = 0.5 if len(indices) == 1 else min(0.9, 0.5 + 0.1 * len(indices))
        results.append(ThemeClusterOutput(
            theme_key=key,
            theme=theme_name,
            theme_tags=top_tags or ["综合"],
            aids=aids,
            source_mix=lens_mix,
            theme_summary=summary_text[:200],
            confidence=conf,
        ))
    return results


async def merge_clusters_with_llm(
    summaries: List[ArticleSummaryRecord],
    clusters: List[List[int]],
    rolling_themes_compact: str,
    settings: Optional[InsightSettings] = None,
) -> List[ThemeClusterOutput]:
    settings = settings or load_insight_settings()
    try:
        ensure_backend_configured(settings, backend=settings.phase_b_backend)
    except RuntimeError:
        logger.warning("Phase B LLM 未配置，使用规则兜底聚类")
        return _fallback_clusters(summaries, clusters)

    candidate_json = _build_candidate_json(summaries, clusters)
    prompt = PHASE_B_PROMPT.format(
        n=len(summaries),
        theme_min=settings.phase_b_theme_min,
        theme_max=settings.phase_b_theme_max,
        candidate_clusters_json=candidate_json,
        rolling_themes_compact=rolling_themes_compact or "（暂无历史主题）",
    )

    try:
        result: ThemeClusterList = await structured_completion(
            prompt,
            ThemeClusterList,
            model=settings.phase_b_model,
            max_tokens=settings.phase_b_max_tokens,
            max_tokens_ceiling=settings.llm_max_tokens_ceiling,
            settings=settings,
            backend=settings.phase_b_backend,
            no_think=settings.phase_b_no_think,
        )
        return result.themes
    except Exception as e:
        logger.warning("Phase B LLM 整合失败，使用规则兜底: %s", e)
        return _fallback_clusters(summaries, clusters)


async def run_clustering(
    summaries: List[ArticleSummaryRecord],
    rolling_themes_compact: str,
    settings: Optional[InsightSettings] = None,
) -> tuple[List[ThemeClusterOutput], Dict[str, List[float]]]:
    """返回 (主题簇, theme_key → centroid embedding)。"""
    settings = settings or load_insight_settings()
    if not summaries:
        return [], {}

    clusters = cluster_by_embedding(summaries, settings.phase_b_distance_threshold)
    themes = await merge_clusters_with_llm(summaries, clusters, rolling_themes_compact, settings)

    # 计算每个主题的 centroid
    aid_to_idx = {s.aid: i for i, s in enumerate(summaries)}
    centroids: Dict[str, List[float]] = {}
    for theme in themes:
        indices = [aid_to_idx[a] for a in theme.aids if a in aid_to_idx]
        if indices:
            c = _cluster_centroid(summaries, indices)
            if c:
                centroids[theme.theme_key] = c
    return themes, centroids
