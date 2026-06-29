# 爬虫数据库设计

> DDL：`worker/db/migrate.py`  
> 写入：`worker/db/repo.py`

## 表：accounts

| 列 | 类型 | 说明 |
|----|------|------|
| `fakeid` | TEXT PK | 公众号 ID |
| `nickname` | TEXT | 显示名 |
| `year_backfill_done` | BOOL | 是否已完成 180 天回填 |

Insight 扩展列（crawl 不直接使用）：`insight_lens`, `insight_tags`, ...

## 表：articles

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGSERIAL | 自增 |
| `fakeid` | TEXT FK | 所属公众号 |
| `aid` | TEXT | 文章 ID（partial unique） |
| `title`, `link` | TEXT | link partial unique |
| `publish_time` | BIGINT | Unix 秒 |
| `digest`, `author`, `cover` | TEXT | 元数据 |
| `content_fetched` | BOOL | 是否抓到正文 |
| `content_len` | INT | 正文字数 |
| `content_source` | TEXT | public/token/local |
| `plain_content` | TEXT | 正文 |
| `crawled_at` | TIMESTAMPTZ | 采集时间 |
| `content_embedding` | vector(1024) | **insight embed 写入，crawl 不写** |

### 去重策略（upsert_articles）

1. 优先 `aid` 匹配更新
2. 无 `aid` 时 fallback `link`

## 表：crawl_runs

| 列 | 说明 |
|----|------|
| `week_id` | 采集周 |
| `start_ts`, `end_ts` | 时间窗 |
| `crawled_at` | 运行时间 |
| `stats` | JSONB，同 manifest.stats |

## 初始化

```bash
python -c "from worker.db.migrate import init_db; init_db()"
```

Docker：

```bash
cd worker && docker compose up -d
# DATABASE_URL=postgresql://wx:wx@localhost:5432/wxspirder
```

## repo 关键函数

| 函数 | 作用 |
|------|------|
| `upsert_account(fakeid, nickname)` | crawl 开始时同步账号 |
| `upsert_articles(fakeid, articles)` | 批量入库 |
| `find_existing_aids( aids )` | 判断 txt 缓存时是否需 db_backfill |
| `get_year_backfill_flags(fakeids)` | 回填门禁 |
| `mark_year_backfill_done(fakeid)` | 标记回填完成 |
| `save_crawl_run(...)` | 记录本次运行 |
