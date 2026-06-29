"""Insight CLI 编排：Phase A → B → C 流水线。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from worker.db import insight_repo
from worker.db.connection import database_available
from worker.db.migrate import init_db
from worker.insight.cluster import run_clustering
from worker.insight.config import load_insight_settings
from worker.insight.embedder import is_configured as embed_configured, run_embed_all
from worker.insight.generator import generate_report, generate_short_report
from worker.insight.profile import run_profile, run_sync_yaml_profiles
from worker.insight.tags import sync_profiles_to_db
from worker.mail.mailer import mailer
from worker.insight.retriever import get_context_for_themes, get_rag_context_for_themes
from worker.insight.selector import InsightSelector
from worker.insight.summarizer import ensure_summaries_for_primary
from worker.insight.themes import compact_rolling_themes, load_active_themes, update_rolling_themes
from worker.insight.validator import validate_report

logger = logging.getLogger(__name__)


class InsightService:
    def __init__(self):
        self.settings = load_insight_settings()

    def run(
        self,
        week: str = "last",
        *,
        dry_run: bool = False,
        skip_embed: bool = False,
        skip_profile: bool = False,
        send_email: Optional[bool] = None,
        email_to: list[str] | None = None,
        publish_blog: Optional[bool] = None,
    ) -> int:
        selector = InsightSelector(week)
        primary, context, stats = selector.select()

        if dry_run:
            self._log_stats(stats)
            return 0

        self._pre_flight(stats)

        if database_available():
            try:
                init_db()
            except Exception as e:
                logger.warning("init_db 跳过: %s", e)

        try:
            sync_profiles_to_db()
        except Exception as e:
            logger.warning("yaml 画像同步跳过: %s", e)

        if not skip_profile:
            try:
                run_profile(dry_run=False)
            except Exception as e:
                logger.warning("账号画像跳过: %s", e)

        if not skip_embed and embed_configured(self.settings):
            try:
                embed_stats = run_embed_all(self.settings)
                logger.info("Embedding 完成: %s", embed_stats)
            except Exception as e:
                logger.warning("Embedding 跳过: %s", e)
        elif not skip_embed:
            logger.warning(
                "Embedding 未配置。请启动 Ollama 并执行: ollama pull %s",
                self.settings.embedding_model,
            )

        if stats.primary_count == 0:
            report = generate_short_report(stats.week_id, "Primary 周无文章")
            self._write_report(
                stats.week_id, report, {"reason": "no_primary"},
                send_email=send_email, email_to=email_to,
                publish_blog=publish_blog,
            )
            return 0

        summaries = asyncio.run(ensure_summaries_for_primary(primary, self.settings))
        summaries = [s for s in summaries if s.quality_score >= 0.4]

        if not summaries:
            report = generate_short_report(
                stats.week_id,
                f"Primary 周 {stats.primary_count} 篇，但无有效摘要（正文覆盖率 {stats.primary_with_content}/{stats.primary_count}）",
            )
            self._write_report(
                stats.week_id, report,
                {"reason": "no_summaries", **stats.model_dump()},
                send_email=send_email, email_to=email_to,
                publish_blog=publish_blog,
            )
            return 0

        rolling = load_active_themes()
        rolling_compact = compact_rolling_themes(rolling)

        themes, centroids = asyncio.run(
            run_clustering(summaries, rolling_compact, self.settings)
        )

        update_rolling_themes(
            themes,
            centroids,
            stats.week_id,
            similarity_threshold=self.settings.rolling_similarity_threshold,
            archive_days=self.settings.rolling_archive_days,
        )

        primary_aids = {s.aid for s in summaries}
        primary_links = {s.aid: s.link for s in summaries if s.link}

        context_text = get_context_for_themes(
            themes,
            centroids,
            stats.week_id,
            limit=self.settings.phase_c_context_themes_limit,
        )

        rag_text, rag_theme_counts, history_index, rag_log = get_rag_context_for_themes(
            themes,
            centroids,
            selector.week_start,
            primary_aids=list(primary_aids),
            primary_summaries=summaries,
            per_theme_limit=self.settings.phase_c_rag_per_theme_limit,
            per_article_limit=self.settings.phase_c_rag_per_article_limit,
            total_limit=self.settings.phase_c_rag_total_limit,
            excerpt_chars=self.settings.phase_c_rag_excerpt_chars,
            min_similarity=self.settings.phase_c_rag_min_similarity,
            content_min_similarity=self.settings.phase_c_rag_content_min_similarity,
            embedding_mode=self.settings.phase_c_rag_embedding_mode,
            tag_filter=self.settings.phase_c_rag_tag_filter,
        )

        report_md, meta = asyncio.run(
            generate_report(
                week_id=stats.week_id,
                themes=themes,
                summaries=summaries,
                context_themes_text=context_text,
                context_articles_text=rag_text,
                rag_theme_counts=rag_theme_counts,
                history_index=history_index,
                settings=self.settings,
            )
        )
        warnings = list(meta.get("aid_warnings") or [])
        warnings.extend(validate_report(report_md, primary_aids, primary_links))
        meta["warnings"] = warnings
        meta["rag_retrieval_log"] = rag_log
        meta.update(stats.model_dump())

        self._write_report(
            stats.week_id, report_md, meta,
            send_email=send_email, email_to=email_to,
            publish_blog=publish_blog,
        )
        logger.info("洞见报告已生成: %s", self.settings.insights_dir / stats.week_id / "report.md")
        if warnings:
            logger.warning("校验警告 %d 条: %s", len(warnings), warnings[:3])
        return 0

    def run_embed(self) -> int:
        if not embed_configured(self.settings):
            logger.error("Embedding backend 未配置")
            return 1
        if database_available():
            init_db()
        stats = run_embed_all(self.settings)
        logger.info(
            "Embedding 完成: summaries=%s, articles=%s",
            stats["summaries"],
            stats["articles"],
        )
        return 0

    def run_profile_cmd(
        self,
        *,
        nickname: Optional[str] = None,
        dry_run: bool = False,
        sync_only: bool = False,
    ) -> int:
        if database_available():
            init_db()
        if sync_only:
            n = run_sync_yaml_profiles()
            logger.info("yaml 同步完成: %d 个账号", n)
            return 0
        n = run_profile(nickname=nickname, dry_run=dry_run)
        logger.info("画像完成: %d 个账号", n)
        return 0

    def _should_send_email(self, override: Optional[bool]) -> bool:
        if override is not None:
            return override
        return self.settings.report_email_enabled

    def _maybe_send_report_email(
        self,
        week_id: str,
        content_md: str,
        meta: dict,
        *,
        send_email: Optional[bool],
        email_to: list[str] | None,
        html_path: Optional[Path] = None,
    ) -> None:
        if not self._should_send_email(send_email):
            return
        try:
            # 优先发 HTML 报告，降级到 Markdown 纯文本
            if html_path and html_path.exists():
                html_body = html_path.read_text(encoding="utf-8")
                mailer.send_insight_report_html(week_id, html_body, to=email_to, meta=meta)
            else:
                mailer.send_insight_report(week_id, content_md, to=email_to, meta=meta)
        except Exception as e:
            logger.warning("洞见报告邮件发送失败: %s", e)

    def _pre_flight(self, stats) -> None:
        if stats.primary_count == 0:
            return
        ratio = stats.primary_with_content / stats.primary_count if stats.primary_count else 0
        if ratio < 0.7:
            logger.warning(
                "正文覆盖率 %.0f%%（%d/%d），洞见质量可能受影响",
                ratio * 100,
                stats.primary_with_content,
                stats.primary_count,
            )

    def _log_stats(self, stats) -> None:
        logger.info("=== Insight dry-run · %s ===", stats.week_id)
        logger.info("数据来源: %s", stats.source)
        logger.info(
            "Primary: %d 篇（有正文 %d）",
            stats.primary_count,
            stats.primary_with_content,
        )
        logger.info("Context 摘要: %d 条", stats.context_summary_count)
        logger.info("L1 lens 分布: %s", stats.lens_distribution)

    def _write_report(
        self,
        week_id: str,
        content_md: str,
        meta: dict,
        *,
        send_email: Optional[bool] = None,
        email_to: list[str] | None = None,
        publish_blog: Optional[bool] = None,
    ) -> None:
        from worker.insight.renderer import render_report_html  # noqa: PLC0415

        out_dir = self.settings.insights_dir / week_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.md").write_text(content_md, encoding="utf-8")
        (out_dir / "report.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            from worker.insight.models import ArticleSummaryRecord, PhaseCReportOutput
            structured_data = meta.get("structured_report")
            summaries_raw = meta.get("summaries_index", [])
            if structured_data:
                report_obj = PhaseCReportOutput.model_validate(structured_data)
                summaries_obj = [
                    ArticleSummaryRecord(
                        aid=s["aid"],
                        fakeid=s.get("fakeid", ""),
                        nickname=s.get("nickname", ""),
                        title=s.get("title", ""),
                        link=s.get("link", ""),
                        summary=s.get("summary", ""),
                        publish_time=int(s.get("publish_time") or 0),
                    )
                    for s in summaries_raw
                ]
                history_index_obj = {
                    s["aid"]: ArticleSummaryRecord(
                        aid=s["aid"],
                        fakeid=s.get("fakeid", ""),
                        nickname=s.get("nickname", ""),
                        title=s.get("title", ""),
                        link=s.get("link", ""),
                        summary=s.get("summary", ""),
                        publish_time=int(s.get("publish_time") or 0),
                    )
                    for s in (meta.get("history_index") or [])
                }
                render_report_html(
                    report_obj, summaries_obj,
                    week_id=week_id,
                    history_index=history_index_obj,
                    output_path=out_dir / "report.html",
                )
            else:
                logger.warning("meta 中无 structured_report，跳过 HTML 渲染")
            logger.info("HTML 报告已生成: %s", out_dir / "report.html")
        except Exception as e:
            logger.warning("HTML 渲染失败，不影响 Markdown: %s", e)
        if database_available():
            try:
                insight_repo.upsert_insight_report(week_id, content_md, meta)
            except Exception as e:
                logger.warning("insights 表写入失败: %s", e)
        html_path = out_dir / "report.html"
        self._maybe_send_report_email(
            week_id, content_md, meta,
            send_email=send_email, email_to=email_to,
            html_path=html_path,
        )
        self._maybe_publish_blog(week_id, publish_blog=publish_blog)

    def _maybe_publish_blog(self, week_id: str, *, publish_blog: Optional[bool] = None) -> None:
        if publish_blog is False:
            return
        if publish_blog is None and not self.settings.blog_publish_on_generate:
            return
        reason = blog_publish_skip_reason(self.settings)
        if reason:
            if publish_blog is True:
                logger.warning("博客发布已请求但未配置（%s），跳过", reason)
            else:
                logger.debug("博客归档未配置（%s），跳过", reason)
            return
        try:
            from worker.insight.publish_blog import publish_week  # noqa: PLC0415

            url = publish_week(week_id, settings=self.settings)
            logger.info("博客已归档: %s", url)
        except Exception as e:
            logger.warning("博客归档失败（不影响报告生成）: %s", e)
