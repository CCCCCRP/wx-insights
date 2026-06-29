"""按 week_id / 时间窗选取 Primary / Context 文章（DB 优先，txt 归档 fallback）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from worker.config import ARCHIVE_ROOT
from worker.crawl.archive import parse_article_txt
from worker.crawl.week import HISTORY_BACKFILL_DAYS, context_range, week_range
from worker.db.connection import database_available
from worker.insight.models import ArticleRecord, ArticleSummaryRecord, SelectionStats
from worker.insight.tags import get_account_lens, get_account_nickname, nickname_by_fakeid

logger = logging.getLogger(__name__)


class InsightSelector:
    def __init__(self, week: str = "last"):
        self.week_start, self.week_end, self.week_id = week_range(week)
        self.ctx_start, self.ctx_end = context_range(self.week_start, HISTORY_BACKFILL_DAYS)

    def select(self) -> Tuple[List[ArticleRecord], List[ArticleSummaryRecord], SelectionStats]:
        if database_available():
            try:
                return self._select_from_db()
            except Exception as e:
                logger.warning("DB 选取失败，fallback 到 txt 归档: %s", e)
        return self._select_from_archive()

    def stats_only(self) -> SelectionStats:
        primary, context, stats = self.select()
        return stats

    def _select_from_db(self) -> Tuple[List[ArticleRecord], List[ArticleSummaryRecord], SelectionStats]:
        from worker.db.insight_repo import fetch_context_summaries, fetch_primary_articles, fetch_summaries_for_aids

        raw_primary = fetch_primary_articles(self.week_start, self.week_end)
        primary: List[ArticleRecord] = []
        for row in raw_primary:
            has_content = bool(row.get("content_fetched") and (row.get("plain_content") or "").strip())
            primary.append(ArticleRecord(
                aid=row.get("aid") or "",
                fakeid=row.get("fakeid") or "",
                nickname=row.get("nickname") or get_account_nickname(row.get("fakeid") or ""),
                title=row.get("title") or "",
                link=row.get("link") or "",
                publish_time=int(row.get("publish_time") or 0),
                plain_content=(row.get("plain_content") or "").strip(),
                digest=row.get("digest") or "",
                content_fetched=bool(row.get("content_fetched")),
                account_lens=row.get("account_lens") or "general",
                low_confidence=not has_content,
            ))

        raw_ctx = fetch_context_summaries(self.ctx_start, self.ctx_end)
        context = [self._summary_from_row(r) for r in raw_ctx]

        # 若 Primary 有 aid 且 DB 已有摘要，补充到 context 查询路径外单独用
        primary_aids = [p.aid for p in primary if p.aid]
        if primary_aids:
            _ = fetch_summaries_for_aids(primary_aids)

        stats = self._build_stats(primary, context, source="db")
        return primary, context, stats

    def _select_from_archive(self) -> Tuple[List[ArticleRecord], List[ArticleSummaryRecord], SelectionStats]:
        primary = self._load_archive_week(self.week_id)
        context: List[ArticleSummaryRecord] = []

        if not ARCHIVE_ROOT.is_dir():
            stats = self._build_stats(primary, context, source="archive")
            return primary, context, stats

        for week_dir in sorted(ARCHIVE_ROOT.iterdir()):
            if not week_dir.is_dir() or week_dir.name == self.week_id:
                continue
            for nickname_dir in week_dir.iterdir():
                if not nickname_dir.is_dir():
                    continue
                for txt in nickname_dir.glob("*.txt"):
                    rec = self._article_from_txt(txt, nickname_dir.name)
                    if not rec or not rec.publish_time:
                        continue
                    if self.ctx_start <= rec.publish_time <= self.ctx_end:
                        cached = self._load_summary_cache(rec.aid)
                        if cached:
                            context.append(cached)

        stats = self._build_stats(primary, context, source="archive")
        return primary, context, stats

    def _load_archive_week(self, week_id: str) -> List[ArticleRecord]:
        week_dir = ARCHIVE_ROOT / week_id
        if not week_dir.is_dir():
            logger.warning("归档目录不存在: %s", week_dir)
            return []

        nick_map = nickname_by_fakeid()
        fakeid_map = {v: k for k, v in nick_map.items()}
        records: List[ArticleRecord] = []

        for nickname_dir in week_dir.iterdir():
            if not nickname_dir.is_dir():
                continue
            nickname = nickname_dir.name
            for txt in nickname_dir.glob("*.txt"):
                rec = self._article_from_txt(txt, nickname)
                if rec:
                    if not rec.fakeid:
                        rec.fakeid = fakeid_map.get(nickname, "")
                    records.append(rec)
        return records

    def _article_from_txt(self, path: Path, nickname: str) -> Optional[ArticleRecord]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, body = parse_article_txt(text)
        aid = meta.get("aid") or path.stem
        fakeid = meta.get("fakeid") or ""
        publish_time = int(meta.get("publish_time") or 0)
        content_fetched = meta.get("content_fetched", "").lower() == "true"
        plain = body.strip() if content_fetched else ""
        return ArticleRecord(
            aid=aid,
            fakeid=fakeid,
            nickname=nickname,
            title=meta.get("title") or "",
            link=meta.get("link") or "",
            publish_time=publish_time,
            plain_content=plain,
            digest=meta.get("digest") or "",
            content_fetched=content_fetched,
            account_lens=get_account_lens(fakeid) if fakeid else "general",
            low_confidence=not (content_fetched and plain),
        )

    def _load_summary_cache(self, aid: str) -> Optional[ArticleSummaryRecord]:
        from worker.insight.config import load_insight_settings
        cache_path = load_insight_settings().summary_cache_dir / f"{aid}.json"
        if not cache_path.is_file():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return ArticleSummaryRecord(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def _summary_from_row(self, row: dict) -> ArticleSummaryRecord:
        emb = row.get("summary_embedding")
        if emb is not None and hasattr(emb, "tolist"):
            emb = emb.tolist()
        return ArticleSummaryRecord(
            aid=row["aid"],
            fakeid=row.get("fakeid") or "",
            nickname=row.get("nickname") or "",
            title=row.get("title") or "",
            link=row.get("link") or "",
            publish_time=int(row.get("publish_time") or 0),
            summary=row.get("summary") or "",
            topic_tags=list(row.get("topic_tags") or []),
            claims=row.get("claims") or [],
            sentiment=row.get("sentiment") or "neutral",
            quality_score=float(row.get("quality_score") or 0.5),
            account_lens=row.get("account_lens") or "general",
            summary_embedding=emb,
        )

    def _build_stats(
        self,
        primary: List[ArticleRecord],
        context: List[ArticleSummaryRecord],
        source: str,
    ) -> SelectionStats:
        lens_dist: dict[str, int] = {}
        for p in primary:
            lens_dist[p.account_lens] = lens_dist.get(p.account_lens, 0) + 1
        with_content = sum(1 for p in primary if p.content_fetched and p.plain_content)
        return SelectionStats(
            week_id=self.week_id,
            primary_count=len(primary),
            primary_with_content=with_content,
            context_summary_count=len(context),
            lens_distribution=lens_dist,
            source=source,
        )
