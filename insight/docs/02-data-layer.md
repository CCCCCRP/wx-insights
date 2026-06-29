# 数据层

---

## Primary vs Context 时间窗

```python
# crawl/week.py
week_start, week_end, week_id = week_range("last")    # Primary
ctx_start, _            = context_range(week_start, 180)  # Context 起点
```

| 层级 | 时间范围 | 角色 | Phase 使用方 |
|------|---------|------|-------------|
| **Primary** | 上一自然周（周一 00:00 ~ 周日 23:59） | 洞见主体，需要正文 | Phase A / B / C |
| **Context** | `[week_start - 180d, week_start)` | 历史对照，只需摘要 | Phase C Context Mirror |

Context 只需要 `article_summaries`，不需要全文。

---

## 数据选取（selector.py）

`InsightSelector(week).select()` → `(primary, context, stats)`

### DB 优先路径

```sql
-- Primary：上周正文
SELECT a.*, acc.insight_lens, acc.nickname
FROM articles a
LEFT JOIN accounts acc ON acc.fakeid = a.fakeid
WHERE publish_time BETWEEN week_start AND week_end

-- Context：历史摘要
SELECT s.*, a.title, a.link, a.publish_time, acc.nickname
FROM article_summaries s
JOIN articles a ON a.aid = s.aid
WHERE a.publish_time BETWEEN ctx_start AND week_start
```

Primary 文章无 `plain_content` 时：`low_confidence=true`（后续摘要跳过，不参与 Phase A）。

### Archive Fallback

触发时机：`DATABASE_URL` 未配置，或 DB 异常。

```
data/archive/{week_id}/{nickname}/*.txt
    → parse_article_txt()  → ArticleRecord（Primary）
其他 week 目录（publish_time 在 Context 窗内）
    → digest 或正文前 500 字 充当简易 summary（Context）
```

**Archive fallback 能力对比：**

| 能力 | DB 模式 | Archive 模式 |
|------|---------|--------------|
| Context 摘要质量 | 高（Phase A 缓存） | 低（digest/截断） |
| embedding | 有 | 无 |
| Rolling Themes | 正常 | 受限 |
| aid 一致性 | 高 | 依赖 frontmatter |

> 生产路径应保证 `DATABASE_URL` 可用；fallback 仅作离线/灾备。

### SelectionStats

```python
SelectionStats:
  week_id                : str
  primary_count          : int   # Primary 总篇数
  primary_with_content   : int   # 有正文（content_fetched=true）的篇数
  context_summary_count  : int   # Context 摘要条数
  lens_distribution      : dict  # lens → 篇数
  source                 : "db" | "archive"
```

---

## 数据库 Schema

> DDL：`worker/db/migrate.py` | CRUD：`worker/db/insight_repo.py`

### articles（crawl 写入，insight 读）

关键列（insight 视角）：

| 列 | 说明 |
|----|------|
| `aid` | 主键（partial unique index，非标准 FK） |
| `plain_content` | 正文，Phase A 输入 |
| `content_embedding` | vector(1024)，Phase 2 RAG 预留 |
| `content_fetched` | 是否已抓正文 |
| `publish_time` | Unix 秒，时间窗过滤 |
| `author` | 作者，从文章页 HTML meta 提取 |

### article_summaries（Phase A 产出）

| 列 | 类型 | 说明 |
|----|------|------|
| `aid` | TEXT PK | 逻辑关联 articles（无 FK：partial unique 限制） |
| `fakeid` | TEXT | 冗余，按号查询 |
| `summary` | TEXT | 150–300 字摘要 |
| `topic_tags` | TEXT[] | L2 标签，3-5 个 |
| `claims` | JSONB | 可核验事实句列表 |
| `sentiment` | TEXT | neutral / bullish / bearish |
| `quality_score` | REAL | 0–1 |
| `account_lens` | TEXT | 冗余 L1（避免 join） |
| `summary_embedding` | vector(1024) | Phase B 聚类核心 |
| `model` | TEXT | 生成摘要的模型 |
| `content_hash` | TEXT | md5(plain_content[:4000])，变更检测 |
| `generated_at` | TIMESTAMPTZ | |

索引：HNSW on `summary_embedding`（m=16, ef_construction=64），`fakeid`，`generated_at`

### themes（Rolling Themes）

| 列 | 说明 |
|----|------|
| `theme_key` | 稳定 kebab-case ID，如 `agent-coding-tools` |
| `display_name` | 中文主题名 |
| `theme_tags` | TEXT[] |
| `theme_embedding` | vector(1024) centroid，ANN 检索用 |
| `timeline` | JSONB 列表（见下） |
| `velocity` | fast / medium / slow |
| `archived` | BOOLEAN，false=active |
| `first_seen_week` / `last_seen_week` | ISO 周 ID |

timeline 元素结构：
```json
{ "week_id": "2026-W25", "status": "active", "article_count": 5, "aids": ["..."] }
```

索引：HNSW on `theme_embedding`

### insights（报告存档）

| 列 | 说明 |
|----|------|
| `week_id` | 唯一键 |
| `content_md` | 全文 Markdown |
| `meta` | JSONB（warnings, stats, model, token_usage） |
| `generated_at` | TIMESTAMPTZ |

### accounts（L1 扩展字段）

| 列 | 说明 |
|----|------|
| `insight_lens` | L1 视角，默认 general |
| `insight_tags` | TEXT[] |
| `insight_profile_source` | auto / manual |
| `insight_profiled_at` | 最近画像时间 |
| `insight_profile_confidence` | 置信度 |
| `insight_profile_locked` | true 时跳过自动画像 |

---

## 向量维度与索引

**当前实现：1024 维**（bge-m3）。如需换模型：

1. `insight.yaml` 改 `embedding.dimensions`
2. `db/migrate.py` 改列维（`ALTER TABLE ... DROP COLUMN / ADD COLUMN vector(N)`）
3. 重新 `insight embed` 全量重算

规模估计：~5000 篇/年 × 1024 float ≈ 20 MB，HNSW 无压力。

---

## 与 crawl 的衔接

| crawl 产出 | insight 消费点 |
|------------|----------------|
| `articles.plain_content` | Phase A 输入 |
| `articles.content_fetched` | selector 过滤 / quality 判断 |
| `articles.publish_time` | Primary/Context 时间窗 |
| `articles.aid`, `link` | 溯源、validator 校验 |
| `data/archive/{week_id}/` | DB 失败 fallback |
| `accounts.fakeid`, `nickname` | lens、nickname 注入 Prompt |

两边共用 `crawl/week.py` 的 `week_range()` 和 `context_range()`，保证 week_id 对齐。

crawl 首次 180 天回填 → DB 积累 Context 所需历史，insight 不需单独 backfill。
