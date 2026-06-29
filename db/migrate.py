"""数据库建表。幂等，可重复执行。"""
from __future__ import annotations

import logging

from worker.db.connection import get_conn

logger = logging.getLogger(__name__)

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

-- ────────────────────────────────────────
-- 基础表
-- ────────────────────────────────────────

-- 微信公众号订阅账号（crawl 写入，insight 读 L1 画像）
CREATE TABLE IF NOT EXISTS accounts (
    -- 微信 searchbiz 返回的 base64 公众号 ID，与 accounts.yaml fakeid 一致
    fakeid              TEXT        PRIMARY KEY,
    -- 公众号显示名称，crawl 时与 yaml 双向同步
    nickname            TEXT        NOT NULL,
    -- crawl 是否已完成近 180 天历史文章回填；true 后才允许 insight 自动画像
    year_backfill_done  BOOLEAN     NOT NULL DEFAULT FALSE,

    -- ── L1 账号画像（insight 模块，见 accounts.yaml insight_* 字段）──
    -- 内容解读视角：industry | interview | science | business | general
    insight_lens             TEXT    DEFAULT 'general',
    -- 账号级话题标签（3–6 个上位概念），注入 Phase A/C Prompt
    insight_tags             TEXT[]  DEFAULT '{}',
    -- 画像来源：auto | auto_bootstrap | manual
    insight_profile_source   TEXT    DEFAULT 'auto',
    -- 最近一次 LLM 自动画像时间（yaml 手工 sync 不写此字段）
    insight_profiled_at      TIMESTAMPTZ,
    -- 画像置信度 0–1；<0.6 时 insight_lens 强制为 general
    insight_profile_confidence REAL,
    -- true 时跳过自动画像，保护 accounts.yaml 手工配置
    insight_profile_locked   BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 文章元数据 + 正文（crawl 写入，insight Phase A 读取 plain_content）
CREATE TABLE IF NOT EXISTS articles (
    id               BIGSERIAL    PRIMARY KEY,
    fakeid           TEXT         NOT NULL REFERENCES accounts(fakeid) ON DELETE CASCADE,
    -- 微信文章 ID（bizmsgid 组合）；partial unique，可为空（仅 link 去重时）
    aid              TEXT,
    title            TEXT         NOT NULL DEFAULT '',
    -- 永久链接 https://mp.weixin.qq.com/s/...
    link             TEXT,
    -- 发布时间 Unix 秒；Primary/Context 时间窗过滤依据
    publish_time     BIGINT,
    -- 列表页摘要（appmsg 接口 digest 字段）
    digest           TEXT,
    -- 作者名，从 /s/ 正文页 <meta name="author"> 提取
    author           TEXT,
    cover            TEXT,
    -- 是否已成功抓取 plain_content
    content_fetched  BOOLEAN      NOT NULL DEFAULT FALSE,
    -- plain_content 字符数
    content_len      INTEGER      NOT NULL DEFAULT 0,
    -- 正文来源：public（/s/ 公开页）| token（公众号 API）| local（本地 txt）
    content_source   TEXT,
    -- 去 HTML 后的纯文本；Phase A 输入，截断至 phase_a.content_truncate_chars
    plain_content    TEXT,
    crawled_at       TIMESTAMPTZ,
    -- 正文向量（Ollama bge-m3，1024 维）；Phase 2 全文 RAG 预留
    content_embedding vector(1024),
    created_at       TIMESTAMPTZ  DEFAULT now(),
    updated_at       TIMESTAMPTZ  DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_aid  ON articles(aid)  WHERE aid  IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_link ON articles(link) WHERE link IS NOT NULL;
CREATE INDEX        IF NOT EXISTS idx_articles_fakeid        ON articles(fakeid);
CREATE INDEX        IF NOT EXISTS idx_articles_publish_time  ON articles(publish_time DESC);
CREATE INDEX        IF NOT EXISTS idx_articles_crawled_at    ON articles(crawled_at DESC);

-- 每次 crawl 运行的审计记录
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          BIGSERIAL    PRIMARY KEY,
    -- ISO 周 ID，如 2026-W25
    week_id     TEXT         NOT NULL,
    -- 本次采集时间窗 [start_ts, end_ts] Unix 秒
    start_ts    BIGINT,
    end_ts      BIGINT,
    crawled_at  TIMESTAMPTZ  DEFAULT now(),
    -- JSONB：篇数、账号数、正文抓取统计等（同 manifest.stats）
    stats       JSONB
);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_week_id ON crawl_runs(week_id);

-- 已有 accounts 表时补 insight 字段（幂等）
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_lens TEXT DEFAULT 'general';
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_tags TEXT[] DEFAULT '{}';
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_profile_source TEXT DEFAULT 'auto';
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_profiled_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_profile_confidence REAL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS insight_profile_locked BOOLEAN DEFAULT FALSE;
-- 正文模板清洗：开头固定文字（空串=无需清洗）；结尾截断标记列表
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS strip_head_pattern TEXT DEFAULT '';
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS strip_tail_markers TEXT[] DEFAULT '{}';

-- ────────────────────────────────────────
-- Insight 模块：Phase A 摘要缓存
-- ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS article_summaries (
    -- 逻辑关联 articles.aid（无 FK：articles.aid 为 partial unique index）
    aid               TEXT PRIMARY KEY,
    -- 冗余公众号 ID，避免 JOIN，加速按号查询与画像
    fakeid            TEXT NOT NULL,
    -- Phase A 产出：150–300 字结构化摘要
    summary           TEXT NOT NULL,
    -- L2 内容标签（快变），3–5 个中文名词短语
    topic_tags        TEXT[]   DEFAULT '{}',
    -- 可核验事实句列表，JSON 数组，至多 3 条
    claims            JSONB    DEFAULT '[]',
    -- 对文中所述趋势的情绪：neutral | bullish | bearish
    sentiment         TEXT,
    -- 信息价值自评 0–1；<0.4 不参与 Phase B/C
    quality_score     REAL,
    -- 冗余 L1 视角快照（写入时 accounts.insight_lens，冷启动为 general）
    account_lens      TEXT,
    -- 摘要向量（bge-m3，1024 维）；Phase B 向量聚类核心输入
    summary_embedding vector(1024),
    -- 生成摘要的 LLM 模型名，如 qwen3:14b
    model             TEXT,
    -- md5(plain_content[:4000])；正文变更则触发重新摘要
    content_hash      TEXT,
    generated_at      TIMESTAMPTZ DEFAULT now()
);

-- HNSW 语义搜索索引（小数据集无需预热，召回率高于 ivfflat）
CREATE INDEX IF NOT EXISTS idx_summaries_embedding
    ON article_summaries
    USING hnsw (summary_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_summaries_fakeid    ON article_summaries(fakeid);
CREATE INDEX IF NOT EXISTS idx_summaries_generated ON article_summaries(generated_at DESC);

-- ────────────────────────────────────────
-- Insight 模块：Rolling Themes（跨周主题追踪）
-- ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS themes (
    id               BIGSERIAL PRIMARY KEY,
    -- 稳定 kebab-case 英文键，如 agent-coding-tools；跨周匹配主键
    theme_key        TEXT      UNIQUE NOT NULL,
    -- 中文主题名（4–10 字），Phase C 报告展示用
    display_name     TEXT      NOT NULL,
    theme_tags       TEXT[]    DEFAULT '{}',
    -- 话题演进速度：fast | medium | slow；Phase 2 动态 Context 窗口依据
    velocity         TEXT      DEFAULT 'medium',
    -- Phase 2 预留：该主题 Context Mirror 回溯天数
    context_days     INTEGER   DEFAULT 180,
    -- 主题质心向量（聚类 centroid 滑动平均）；pgvector ANN 跨周匹配
    theme_embedding  vector(1024),
    -- 每周快照 JSONB 数组，元素：{week_id, status, article_count, theme_summary, confidence, aids}
    timeline         JSONB     NOT NULL DEFAULT '[]',
    -- 叙事链 JSONB 数组，元素：{chain_id, events[]}
    narrative_chains JSONB     DEFAULT '[]',
    first_seen_week  TEXT,
    last_seen_week   TEXT,
    -- true 表示长期无更新已归档，不参与 Rolling Themes 参考
    archived         BOOLEAN   DEFAULT FALSE,
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_themes_embedding
    ON themes
    USING hnsw (theme_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_themes_active ON themes(archived) WHERE archived = FALSE;
CREATE INDEX IF NOT EXISTS idx_themes_last_seen ON themes(last_seen_week DESC);

-- ────────────────────────────────────────
-- Insight 模块：正文向量 HNSW 索引（Phase 2 RAG）
-- ────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_articles_content_embedding
    ON articles
    USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ────────────────────────────────────────
-- Insight 模块：周报存档
-- ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS insights (
    id           BIGSERIAL PRIMARY KEY,
    -- ISO 周 ID，唯一键，与 crawl/selector week_id 对齐
    week_id      TEXT      UNIQUE NOT NULL,
    -- Phase C 产出的 Markdown 洞见报告全文
    content_md   TEXT      NOT NULL,
    -- JSONB：token_usage, model, warnings, n_primary, n_themes, lens_distribution 等
    meta         JSONB,
    generated_at TIMESTAMPTZ DEFAULT now()
);

--  schema 版本追踪（如 vector 维度迁移）
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""

# PostgreSQL 原生列注释（psql \\d+ accounts 可见）；幂等，可重复执行
_COLUMN_COMMENTS = """
COMMENT ON TABLE accounts IS '微信公众号订阅账号：crawl 同步 nickname，insight 维护 L1 画像';
COMMENT ON COLUMN accounts.fakeid IS '微信 searchbiz 返回的 base64 公众号 ID（主键）';
COMMENT ON COLUMN accounts.nickname IS '公众号显示名称，与 accounts.yaml 同步';
COMMENT ON COLUMN accounts.year_backfill_done IS '是否已完成近 180 天历史回填；true 后才允许 insight 自动画像';
COMMENT ON COLUMN accounts.insight_lens IS 'L1 内容视角：industry|interview|science|business|general';
COMMENT ON COLUMN accounts.insight_tags IS 'L1 话题标签数组，注入 Phase A/C Prompt';
COMMENT ON COLUMN accounts.insight_profile_source IS '画像来源：auto|auto_bootstrap|manual';
COMMENT ON COLUMN accounts.insight_profiled_at IS '最近一次 LLM 自动画像时间';
COMMENT ON COLUMN accounts.insight_profile_confidence IS '画像置信度 0–1；<0.6 强制 general';
COMMENT ON COLUMN accounts.insight_profile_locked IS 'true 时跳过自动画像，保护 yaml 手工配置';
COMMENT ON COLUMN accounts.created_at IS '记录创建时间';
COMMENT ON COLUMN accounts.updated_at IS '记录最后更新时间';

COMMENT ON TABLE articles IS '文章元数据与正文：crawl 写入，insight Phase A 读取';
COMMENT ON COLUMN articles.id IS '自增主键（内部使用）';
COMMENT ON COLUMN articles.fakeid IS '所属公众号 ID（FK → accounts.fakeid）';
COMMENT ON COLUMN articles.aid IS '微信文章 ID；partial unique，可为空';
COMMENT ON COLUMN articles.title IS '文章标题';
COMMENT ON COLUMN articles.link IS '永久链接 https://mp.weixin.qq.com/s/...';
COMMENT ON COLUMN articles.publish_time IS '发布时间 Unix 秒；时间窗过滤依据';
COMMENT ON COLUMN articles.digest IS '列表页摘要（appmsg digest）';
COMMENT ON COLUMN articles.author IS '作者名，从 HTML meta name=author 提取';
COMMENT ON COLUMN articles.cover IS '封面图 URL';
COMMENT ON COLUMN articles.content_fetched IS '是否已成功抓取 plain_content';
COMMENT ON COLUMN articles.content_len IS 'plain_content 字符数';
COMMENT ON COLUMN articles.content_source IS '正文来源：public|token|local';
COMMENT ON COLUMN articles.plain_content IS '纯文本正文；Phase A LLM 输入';
COMMENT ON COLUMN articles.crawled_at IS '最后一次 crawl 采集时间';
COMMENT ON COLUMN articles.content_embedding IS '正文向量 bge-m3 1024 维；Phase 2 RAG';
COMMENT ON COLUMN articles.created_at IS '记录创建时间';
COMMENT ON COLUMN articles.updated_at IS '记录最后更新时间';

COMMENT ON TABLE crawl_runs IS 'crawl 运行审计：每次按周采集一条记录';
COMMENT ON COLUMN crawl_runs.id IS '自增主键';
COMMENT ON COLUMN crawl_runs.week_id IS 'ISO 周 ID，如 2026-W25';
COMMENT ON COLUMN crawl_runs.start_ts IS '采集时间窗起始 Unix 秒';
COMMENT ON COLUMN crawl_runs.end_ts IS '采集时间窗结束 Unix 秒';
COMMENT ON COLUMN crawl_runs.crawled_at IS '运行完成时间';
COMMENT ON COLUMN crawl_runs.stats IS 'JSONB 统计：篇数、账号数、正文抓取等';

COMMENT ON TABLE article_summaries IS 'Phase A 结构化摘要缓存';
COMMENT ON COLUMN article_summaries.aid IS '逻辑 PK，关联 articles.aid（无 FK）';
COMMENT ON COLUMN article_summaries.fakeid IS '冗余公众号 ID，加速按号查询';
COMMENT ON COLUMN article_summaries.summary IS '150–300 字结构化摘要';
COMMENT ON COLUMN article_summaries.topic_tags IS 'L2 内容标签 3–5 个';
COMMENT ON COLUMN article_summaries.claims IS '可核验事实句 JSON 数组';
COMMENT ON COLUMN article_summaries.sentiment IS 'neutral|bullish|bearish';
COMMENT ON COLUMN article_summaries.quality_score IS '信息价值 0–1；<0.4 不参与 Phase B/C';
COMMENT ON COLUMN article_summaries.account_lens IS '冗余 L1 视角快照';
COMMENT ON COLUMN article_summaries.summary_embedding IS '摘要向量；Phase B 聚类输入';
COMMENT ON COLUMN article_summaries.model IS '生成摘要的 LLM 模型名';
COMMENT ON COLUMN article_summaries.content_hash IS 'md5(plain_content[:4000])；变更检测';
COMMENT ON COLUMN article_summaries.generated_at IS '摘要生成时间';

COMMENT ON TABLE themes IS 'Rolling Themes：跨周主题追踪与 Context Mirror';
COMMENT ON COLUMN themes.id IS '自增主键';
COMMENT ON COLUMN themes.theme_key IS '稳定 kebab-case 主题 ID';
COMMENT ON COLUMN themes.display_name IS '中文主题名';
COMMENT ON COLUMN themes.theme_tags IS '主题标签数组';
COMMENT ON COLUMN themes.velocity IS '话题速度：fast|medium|slow';
COMMENT ON COLUMN themes.context_days IS 'Phase 2 Context 回溯天数';
COMMENT ON COLUMN themes.theme_embedding IS '主题质心向量；跨周 ANN 匹配';
COMMENT ON COLUMN themes.timeline IS '每周快照 JSONB 数组';
COMMENT ON COLUMN themes.narrative_chains IS '叙事链 JSONB 数组';
COMMENT ON COLUMN themes.first_seen_week IS '首次出现 ISO 周 ID';
COMMENT ON COLUMN themes.last_seen_week IS '末次出现 ISO 周 ID';
COMMENT ON COLUMN themes.archived IS 'true=已归档，不参与 Rolling 参考';
COMMENT ON COLUMN themes.updated_at IS '最后更新时间';

COMMENT ON TABLE insights IS 'Weekly Insights 洞见报告存档';
COMMENT ON COLUMN insights.id IS '自增主键';
COMMENT ON COLUMN insights.week_id IS 'ISO 周 ID（唯一）';
COMMENT ON COLUMN insights.content_md IS 'Markdown 报告全文';
COMMENT ON COLUMN insights.meta IS 'JSONB：token_usage, warnings, stats 等';
COMMENT ON COLUMN insights.generated_at IS '报告生成时间';

COMMENT ON TABLE schema_migrations IS 'DDL 版本追踪';
COMMENT ON COLUMN schema_migrations.version IS '迁移版本标识';
COMMENT ON COLUMN schema_migrations.applied_at IS '迁移执行时间';
"""

# 1536 → 1024 向量维度迁移（切换本地 embedding 时执行一次，清空旧向量）
_VECTOR_1024_MIGRATION = """
DROP INDEX IF EXISTS idx_summaries_embedding;
DROP INDEX IF EXISTS idx_themes_embedding;
DROP INDEX IF EXISTS idx_articles_content_embedding;
ALTER TABLE article_summaries DROP COLUMN IF EXISTS summary_embedding;
ALTER TABLE article_summaries ADD COLUMN summary_embedding vector(1024);
ALTER TABLE themes DROP COLUMN IF EXISTS theme_embedding;
ALTER TABLE themes ADD COLUMN theme_embedding vector(1024);
ALTER TABLE articles DROP COLUMN IF EXISTS content_embedding;
ALTER TABLE articles ADD COLUMN content_embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_summaries_embedding
    ON article_summaries USING hnsw (summary_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_themes_embedding
    ON themes USING hnsw (theme_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_articles_content_embedding
    ON articles USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
INSERT INTO schema_migrations (version) VALUES ('vector_1024');
"""


def init_db() -> None:
    """创建所有表和索引（幂等）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_COLUMN_COMMENTS)
            cur.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s",
                ("vector_1024",),
            )
            if not cur.fetchone():
                logger.info("执行向量维度迁移 1536→1024（清空旧 embedding）")
                cur.execute(_VECTOR_1024_MIGRATION)
    logger.info("数据库初始化完成")
