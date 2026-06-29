"""Insight 相关数据库读写。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from worker.db.connection import get_conn

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))


def _row_to_dict(cols: Sequence[str], row: tuple) -> Dict[str, Any]:
    return dict(zip(cols, row))


def _as_vector(embedding: List[float]):
    """pgvector 查询/写入参数；裸 list 会被 PostgreSQL 当成 numeric[]。"""
    from pgvector import Vector

    return Vector(embedding)


def fetch_primary_articles(week_start: int, week_end: int) -> List[Dict[str, Any]]:
    sql = """
        SELECT a.aid, a.fakeid, a.title, a.link, a.publish_time,
               a.plain_content, a.digest, a.content_fetched,
               COALESCE(acc.insight_lens, 'general') AS account_lens,
               COALESCE(acc.nickname, '') AS nickname
        FROM articles a
        LEFT JOIN accounts acc ON acc.fakeid = a.fakeid
        WHERE a.publish_time BETWEEN %s AND %s
        ORDER BY a.publish_time DESC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (week_start, week_end))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_context_summaries(ctx_start: int, ctx_end: int) -> List[Dict[str, Any]]:
    sql = """
        SELECT s.aid, s.fakeid, s.summary, s.topic_tags, s.claims,
               s.sentiment, s.quality_score, s.account_lens,
               s.summary_embedding, a.title, a.link, a.publish_time,
               COALESCE(acc.nickname, '') AS nickname
        FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        LEFT JOIN accounts acc ON acc.fakeid = s.fakeid
        WHERE a.publish_time BETWEEN %s AND %s
        ORDER BY a.publish_time DESC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ctx_start, ctx_end))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_summaries_for_aids(aids: List[str]) -> List[Dict[str, Any]]:
    if not aids:
        return []
    sql = """
        SELECT s.*, a.title, a.link, a.publish_time,
               COALESCE(acc.nickname, '') AS nickname
        FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        LEFT JOIN accounts acc ON acc.fakeid = s.fakeid
        WHERE s.aid = ANY(%s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (aids,))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_articles_needing_summary(ctx_start: int) -> List[Dict[str, Any]]:
    sql = """
        SELECT a.aid, a.fakeid, a.title, a.link, a.publish_time,
               a.plain_content, a.digest, a.content_fetched,
               COALESCE(acc.insight_lens, 'general') AS account_lens,
               COALESCE(acc.nickname, '') AS nickname,
               s.content_hash AS existing_hash
        FROM articles a
        JOIN accounts acc ON acc.fakeid = a.fakeid
        LEFT JOIN article_summaries s ON s.aid = a.aid
        WHERE a.content_fetched = true
          AND a.plain_content IS NOT NULL
          AND a.plain_content != ''
          AND a.publish_time >= %s
          AND a.aid IS NOT NULL
          AND (
              s.aid IS NULL
              OR s.content_hash IS DISTINCT FROM md5(left(a.plain_content, 4000))
          )
        ORDER BY a.publish_time DESC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ctx_start,))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def upsert_summary(
    *,
    aid: str,
    fakeid: str,
    summary: str,
    topic_tags: List[str],
    claims: List[Any],
    sentiment: str,
    quality_score: float,
    account_lens: str,
    content_hash: str,
    model: str,
    summary_embedding: Optional[List[float]] = None,
) -> None:
    sql = """
        INSERT INTO article_summaries (
            aid, fakeid, summary, topic_tags, claims, sentiment,
            quality_score, account_lens, summary_embedding, model, content_hash
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (aid) DO UPDATE SET
            fakeid = EXCLUDED.fakeid,
            summary = EXCLUDED.summary,
            topic_tags = EXCLUDED.topic_tags,
            claims = EXCLUDED.claims,
            sentiment = EXCLUDED.sentiment,
            quality_score = EXCLUDED.quality_score,
            account_lens = EXCLUDED.account_lens,
            summary_embedding = COALESCE(EXCLUDED.summary_embedding, article_summaries.summary_embedding),
            model = EXCLUDED.model,
            content_hash = EXCLUDED.content_hash,
            generated_at = now()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                aid, fakeid, summary, topic_tags,
                json.dumps(claims, ensure_ascii=False),
                sentiment, quality_score, account_lens,
                summary_embedding, model, content_hash,
            ))


def fetch_summaries_missing_embedding(limit: int = 500) -> List[Dict[str, Any]]:
    sql = """
        SELECT aid, summary FROM article_summaries
        WHERE summary_embedding IS NULL AND summary IS NOT NULL
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return [{"aid": r[0], "summary": r[1]} for r in cur.fetchall()]


def update_summary_embedding(aid: str, embedding: List[float]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE article_summaries SET summary_embedding = %s WHERE aid = %s",
                (embedding, aid),
            )


def update_summary_embeddings_batch(pairs: List[Tuple[List[float], str]]) -> None:
    """批量更新摘要 embedding，pairs = [(embedding, aid), ...]"""
    if not pairs:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE article_summaries SET summary_embedding = %s WHERE aid = %s",
                pairs,
            )


def fetch_articles_missing_content_embedding(limit: int = 500) -> List[Dict[str, Any]]:
    sql = """
        SELECT aid, fakeid, plain_content FROM articles
        WHERE content_embedding IS NULL
          AND content_fetched = true
          AND plain_content IS NOT NULL
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return [{"aid": r[0], "fakeid": r[1], "plain_content": r[2]} for r in cur.fetchall()]


def update_article_content_embedding(aid: str, embedding: List[float]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET content_embedding = %s, updated_at = now() WHERE aid = %s",
                (embedding, aid),
            )


def update_article_content_embeddings_batch(pairs: List[Tuple[List[float], str]]) -> None:
    """批量更新正文 embedding，pairs = [(embedding, aid), ...]"""
    if not pairs:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE articles SET content_embedding = %s, updated_at = now() WHERE aid = %s",
                pairs,
            )


def count_summaries_with_embedding_for_week(week_start: int, week_end: int) -> int:
    sql = """
        SELECT COUNT(*) FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        WHERE a.publish_time BETWEEN %s AND %s
          AND s.summary_embedding IS NOT NULL
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (week_start, week_end))
            row = cur.fetchone()
            return int(row[0]) if row else 0


def lookup_aid_by_link(link: str) -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT aid FROM articles WHERE link = %s LIMIT 1", (link,))
            row = cur.fetchone()
            return row[0] if row else None


def lookup_links_by_aids(aids: List[str]) -> Dict[str, str]:
    if not aids:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT aid, link FROM articles WHERE aid = ANY(%s)", (aids,))
            return {r[0]: r[1] or "" for r in cur.fetchall()}


def fetch_active_themes(limit: int = 50) -> List[Dict[str, Any]]:
    sql = """
        SELECT theme_key, display_name, theme_tags, velocity, context_days,
               timeline, narrative_chains, first_seen_week, last_seen_week
        FROM themes
        WHERE archived = FALSE
        ORDER BY last_seen_week DESC NULLS LAST
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_theme_by_key(theme_key: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, theme_key, display_name, theme_tags, timeline, archived
        FROM themes
        WHERE theme_key = %s
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (theme_key,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "theme_key": row[1],
                "display_name": row[2],
                "theme_tags": row[3],
                "timeline": row[4],
                "archived": bool(row[5]),
            }


def find_similar_theme(embedding: List[float], threshold: float = 0.72) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, theme_key, display_name, theme_tags, timeline,
               1 - (theme_embedding <=> %s) AS similarity
        FROM themes
        WHERE archived = FALSE AND theme_embedding IS NOT NULL
        ORDER BY theme_embedding <=> %s
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            vec = _as_vector(embedding)
            cur.execute(sql, (vec, vec))
            row = cur.fetchone()
            if not row:
                return None
            sim = float(row[5])
            if sim < threshold:
                return None
            return {
                "id": row[0],
                "theme_key": row[1],
                "display_name": row[2],
                "theme_tags": row[3],
                "timeline": row[4],
                "similarity": sim,
            }


def insert_theme(
    *,
    theme_key: str,
    display_name: str,
    theme_tags: List[str],
    velocity: str,
    theme_embedding: List[float],
    timeline_entry: Dict[str, Any],
    week_id: str,
) -> None:
    sql = """
        INSERT INTO themes (
            theme_key, display_name, theme_tags, velocity,
            theme_embedding, timeline, first_seen_week, last_seen_week
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                theme_key, display_name, theme_tags, velocity,
                _as_vector(theme_embedding),
                json.dumps([timeline_entry], ensure_ascii=False),
                week_id, week_id,
            ))


def append_theme_timeline(
    theme_id: int,
    timeline_entry: Dict[str, Any],
    week_id: str,
    theme_embedding: List[float],
    display_name: Optional[str] = None,
    theme_tags: Optional[List[str]] = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT timeline FROM themes WHERE id = %s", (theme_id,))
            row = cur.fetchone()
            timeline = row[0] if row and row[0] else []
            if isinstance(timeline, str):
                timeline = json.loads(timeline)
            timeline = list(timeline)
            timeline.append(timeline_entry)

            sets = [
                "timeline = %s::jsonb",
                "last_seen_week = %s",
                "theme_embedding = %s",
                "updated_at = now()",
            ]
            params: List[Any] = [
                json.dumps(timeline, ensure_ascii=False),
                week_id,
                _as_vector(theme_embedding),
            ]
            if display_name:
                sets.append("display_name = %s")
                params.append(display_name)
            if theme_tags:
                sets.append("theme_tags = %s")
                params.append(theme_tags)
            params.append(theme_id)

            cur.execute(
                f"UPDATE themes SET {', '.join(sets)} WHERE id = %s",
                params,
            )


def unarchive_theme(theme_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE themes SET archived = FALSE, updated_at = now() WHERE id = %s",
                (theme_id,),
            )


def archive_stale_themes(cutoff_week_id: str) -> int:
    """归档 last_seen_week 早于 cutoff 的主题。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE themes SET archived = TRUE, updated_at = now()
                WHERE archived = FALSE
                  AND last_seen_week IS NOT NULL
                  AND last_seen_week < %s
                """,
                (cutoff_week_id,),
            )
            return cur.rowcount


def fetch_similar_summaries_for_embedding(
    embedding: List[float],
    publish_time_min: int,
    publish_time_max: int,
    limit: int = 5,
    exclude_aids: Optional[List[str]] = None,
    min_similarity: float = 0.5,
) -> List[Dict[str, Any]]:
    """按 summary_embedding 检索历史文章（摘要同空间 Top-K）。

    查询向量通常为 Phase B centroid 或本周 Primary 单篇 summary_embedding。
    plain_content 在 SQL 层截断为前 1000 字，避免全量拉取大字段。
    min_similarity 过滤低相关结果，默认 0.5（cosine）。
    """
    exclude = exclude_aids or []
    sql = """
        SELECT a.aid, a.title, a.link, a.publish_time,
               LEFT(a.plain_content, 1000) AS plain_content,
               s.summary, s.topic_tags,
               COALESCE(acc.nickname, '') AS nickname,
               1 - (s.summary_embedding <=> %s) AS similarity
        FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        LEFT JOIN accounts acc ON acc.fakeid = a.fakeid
        WHERE s.summary_embedding IS NOT NULL
          AND s.summary IS NOT NULL
          AND a.publish_time >= %s
          AND a.publish_time <= %s
          AND NOT (a.aid = ANY(%s))
          AND (1 - (s.summary_embedding <=> %s)) >= %s
        ORDER BY s.summary_embedding <=> %s
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            vec = _as_vector(embedding)
            cur.execute(
                sql,
                (
                    vec,
                    publish_time_min, publish_time_max,
                    exclude,
                    vec, min_similarity,
                    vec,
                    limit,
                ),
            )
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_similar_by_content_embedding(
    embedding: List[float],
    publish_time_min: int,
    publish_time_max: int,
    limit: int = 5,
    exclude_aids: Optional[List[str]] = None,
    min_similarity: float = 0.5,
) -> List[Dict[str, Any]]:
    """按 content_embedding 检索历史文章（正文向量 Top-K，兼容未做 Phase A 的历史库）。"""
    exclude = exclude_aids or []
    sql = """
        SELECT a.aid, a.title, a.link, a.publish_time,
               LEFT(a.plain_content, 1000) AS plain_content,
               s.summary, s.topic_tags,
               COALESCE(acc.nickname, '') AS nickname,
               1 - (a.content_embedding <=> %s) AS similarity
        FROM articles a
        LEFT JOIN article_summaries s ON s.aid = a.aid
        LEFT JOIN accounts acc ON acc.fakeid = a.fakeid
        WHERE a.content_embedding IS NOT NULL
          AND a.publish_time >= %s
          AND a.publish_time <= %s
          AND NOT (a.aid = ANY(%s))
          AND (1 - (a.content_embedding <=> %s)) >= %s
        ORDER BY a.content_embedding <=> %s
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            vec = _as_vector(embedding)
            cur.execute(
                sql,
                (
                    vec,
                    publish_time_min, publish_time_max,
                    exclude,
                    vec, min_similarity,
                    vec,
                    limit,
                ),
            )
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_context_themes_for_embedding(
    embedding: List[float],
    cutoff_week_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    sql = """
        SELECT display_name, theme_tags, timeline, velocity,
               1 - (theme_embedding <=> %s) AS similarity
        FROM themes
        WHERE archived = FALSE
          AND theme_embedding IS NOT NULL
          AND (last_seen_week IS NULL OR last_seen_week >= %s)
        ORDER BY theme_embedding <=> %s
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            vec = _as_vector(embedding)
            cur.execute(sql, (vec, cutoff_week_id, vec, limit))
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def upsert_insight_report(week_id: str, content_md: str, meta: Dict[str, Any]) -> None:
    sql = """
        INSERT INTO insights (week_id, content_md, meta)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (week_id) DO UPDATE SET
            content_md = EXCLUDED.content_md,
            meta = EXCLUDED.meta,
            generated_at = now()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (week_id, content_md, json.dumps(meta, ensure_ascii=False)))


def fetch_account_for_profile(fakeid: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT fakeid, nickname, year_backfill_done,
               insight_lens, insight_tags, insight_profile_locked,
               insight_profiled_at, insight_profile_confidence,
               strip_head_pattern, strip_tail_markers
        FROM accounts WHERE fakeid = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return _row_to_dict(cols, row)


def count_quality_summaries(fakeid: str, since_ts: int) -> int:
    sql = """
        SELECT COUNT(*) FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        WHERE s.fakeid = %s
          AND s.quality_score >= 0.4
          AND a.publish_time >= %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, since_ts))
            row = cur.fetchone()
            return int(row[0]) if row else 0


def fetch_summaries_for_account(fakeid: str, since_ts: int) -> List[Dict[str, Any]]:
    sql = """
        SELECT s.aid, s.summary, s.topic_tags, a.title
        FROM article_summaries s
        JOIN articles a ON a.aid = s.aid
        WHERE s.fakeid = %s AND a.publish_time >= %s
        ORDER BY a.publish_time DESC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, since_ts))
            return [
                {"aid": r[0], "summary": r[1], "topic_tags": r[2], "title": r[3]}
                for r in cur.fetchall()
            ]


def update_account_profile(
    fakeid: str,
    *,
    insight_lens: str,
    insight_tags: List[str],
    confidence: float,
    source: str = "auto",
    strip_head_pattern: str = "",
    strip_tail_markers: Optional[List[str]] = None,
) -> None:
    sql = """
        UPDATE accounts SET
            insight_lens = %s,
            insight_tags = %s,
            insight_profile_source = %s,
            insight_profiled_at = now(),
            insight_profile_confidence = %s,
            strip_head_pattern = COALESCE(NULLIF(%s, ''), strip_head_pattern),
            strip_tail_markers = CASE WHEN %s::text[] IS NOT NULL AND array_length(%s::text[], 1) > 0
                                      THEN %s::text[]
                                      ELSE strip_tail_markers END,
            updated_at = now()
        WHERE fakeid = %s
    """
    markers = strip_tail_markers or []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                insight_lens, insight_tags, source, confidence,
                strip_head_pattern,
                markers, markers, markers,
                fakeid,
            ))


def fetch_raw_content_snippets_for_account(
    fakeid: str,
    *,
    limit: int = 3,
    head_chars: int = 200,
    tail_chars: int = 200,
) -> List[Dict[str, str]]:
    """返回最近 N 篇文章的正文头尾片段，供 profile LLM 识别 boilerplate。"""
    sql = """
        SELECT title, plain_content FROM articles
        WHERE fakeid = %s AND plain_content IS NOT NULL AND content_len > 500
        ORDER BY publish_time DESC
        LIMIT %s
    """
    results = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, limit))
            for title, content in cur.fetchall():
                if not content:
                    continue
                results.append({
                    "title": title or "",
                    "head": content[:head_chars],
                    "tail": content[-tail_chars:],
                })
    return results


def fetch_strip_rules_for_all_accounts() -> Dict[str, Dict[str, Any]]:
    """返回 {fakeid: {strip_head_pattern, strip_tail_markers}} 供 content_clean 缓存。"""
    sql = """
        SELECT fakeid, strip_head_pattern, strip_tail_markers
        FROM accounts
        WHERE strip_head_pattern != '' OR array_length(strip_tail_markers, 1) > 0
    """
    out: Dict[str, Dict[str, Any]] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for fakeid, head, tail in cur.fetchall():
                out[fakeid] = {
                    "strip_head_pattern": head or "",
                    "strip_tail_markers": tail or [],
                }
    return out


def fetch_article_titles_for_account(
    fakeid: str,
    since_ts: int,
    *,
    limit: int = 40,
) -> List[str]:
    sql = """
        SELECT title FROM articles
        WHERE fakeid = %s AND publish_time >= %s AND title IS NOT NULL AND title != ''
        ORDER BY publish_time DESC
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, since_ts, limit))
            return [r[0] for r in cur.fetchall() if r[0]]


def count_articles_for_account(fakeid: str, since_ts: int) -> int:
    sql = """
        SELECT COUNT(*) FROM articles
        WHERE fakeid = %s AND publish_time >= %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, since_ts))
            row = cur.fetchone()
            return int(row[0]) if row else 0


def sync_account_profile_from_yaml(
    fakeid: str,
    *,
    insight_lens: str,
    insight_tags: List[str],
    locked: bool = False,
) -> None:
    """将 yaml 手工配置写入 DB；locked 账号不会被 auto 画像覆盖 lens/tags。"""
    sql = """
        UPDATE accounts SET
            insight_lens = %s,
            insight_tags = %s,
            insight_profile_locked = %s,
            insight_profile_source = CASE
                WHEN %s THEN 'manual'
                ELSE COALESCE(insight_profile_source, 'auto')
            END,
            updated_at = now()
        WHERE fakeid = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (insight_lens, insight_tags, locked, locked, fakeid),
            )


def list_accounts_for_profiling() -> List[Dict[str, Any]]:
    sql = """
        SELECT fakeid, nickname, year_backfill_done,
               insight_profile_locked, insight_profiled_at
        FROM accounts
        ORDER BY nickname
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in cur.fetchall()]


def week_id_minus_days(week_id: str, days: int) -> str:
    """从 week_id 推算 N 天前的近似 week_id（用于归档 cutoff）。"""
    try:
        year = int(week_id[:4])
        week_num = int(week_id.split("-W")[1])
        monday = datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u")
        monday = monday.replace(tzinfo=TZ_CN)
        target = monday - timedelta(days=days)
        return target.strftime("%G-W%V")
    except (ValueError, IndexError):
        cutoff = datetime.now(TZ_CN) - timedelta(days=days)
        return cutoff.strftime("%G-W%V")
