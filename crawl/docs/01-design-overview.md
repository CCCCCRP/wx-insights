# 爬虫整体设计（Crawl Design Overview）

> 版本：v1.0 · 2026-06-27  
> 代码入口：`worker/crawl/service.py` → `CrawlService.run()`  
> CLI：`python -m worker crawl [--week last|2026-W25] [--no-content] [--force-content]`

---

## 1. 目标

在**已登录微信公众平台凭证**的前提下，自动化完成：

1. 按**自然周**拉取配置公众号的文章列表（元数据）
2. 可选抓取每篇 `/s/` **正文**（公开页优先，token 回退）
3. 写入**本地 txt 归档**（人类可读 + insight fallback）
4. 写入 **PostgreSQL**（结构化查询 + embedding 下游）

**非目标**：洞见分析、LLM 摘要、邮件推送报告（由 `insight/` 负责）。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **采集与分析解耦** | crawl 只产出 `articles` + archive；insight 通过 `week_id` / `publish_time` 消费 |
| **双写冗余** | DB 为主查询面；txt 为离线备份与 DB 不可用时的 fallback |
| **增量友好** | 本地已有详细正文则跳过网页抓取；DB upsert 按 `aid`/`link` 去重 |
| **首次回填** | 新订阅公众号自动拉取近 **180 天**历史，按文章发布周写入对应 `archive/{week_id}/` |
| **凭证外置** | token/cookie 由 `WECHAT_API_DIR` 下 `auth_manager` 管理，crawl 只读取 |

---

## 3. 总体架构

```
┌──────────────┐     ┌─────────────────────────────────────────────────┐
│ login/scan   │────▶│ WECHAT_API_DIR / auth_manager                   │
│ (前置)       │     │ → token + cookie 持久化                          │
└──────────────┘     └────────────────────────┬────────────────────────┘
                                              │
┌──────────────┐     ┌────────────────────────▼────────────────────────┐
│ accounts.yaml│────▶│ CrawlService                                     │
└──────────────┘     │  ① week_range → 本周时间窗                        │
                     │  ② 每号：可选 year_backfill（180d）               │
                     │  ③ ArticleFetcher → 列表 API                      │
                     │  ④ ArticlePageFetcher → 正文（public→token）      │
                     │  ⑤ archive.py → txt                               │
                     │  ⑥ db/repo → PostgreSQL                           │
                     └────────────┬───────────────────┬──────────────────┘
                                  ▼                   ▼
                     data/archive/{week_id}/      articles, crawl_runs
```

---

## 4. 核心组件

### 4.1 CrawlService（编排器）

文件：`service.py`

职责：
- 登录门禁 `_ensure_login()`：无 token 时轮询等待或失败
- 加载账号 `load_accounts(resolve=True)`：缺 `fakeid` 时调 `BizSearcher` 搜索补全
- **近半年回填** `_run_year_backfill()`：每号仅一次（`accounts.year_backfill_done`）
- **周采集** `fetcher.fetch_week(fakeid, start_ts, end_ts)`
- **正文处理** `_process_articles()`：缓存判断 → enrich → 写 txt
- **收尾** `write_manifest()` + `save_crawl_run()`

### 4.2 ArticleFetcher（列表）

文件：`fetcher.py`

- 调用 `mp.weixin.qq.com/cgi-bin/appmsgpublish`
- 分页拉取，过滤 `publish_time` 落在 `[start_ts, end_ts]`
- 输出字段：`aid`, `title`, `link`, `digest`, `cover`, `author`, `publish_time`

### 4.3 ArticlePageFetcher（正文）

文件：`content_fetcher.py` + `article_content.py`

策略：**公开页优先 → token 回退**

1. `_fetch_public(link)`：无凭证访问 `/s/` HTML，`parse_article_html()` 提取 `#js_content`
2. 失败/被拦/空正文 → `_fetch_with_token()`：带 token+cookie，复用凭证服务的 `content_processor`
3. 每次请求后 `sleep(ARTICLE_FETCH_DELAY)`，默认 1.5s

### 4.4 Archive（本地归档）

文件：`archive.py`

- 路径：`data/archive/{week_id}/{nickname}/{YYYYMMDD}_{title}.txt`
- frontmatter + 正文；`is_detailed_archive()` 判断是否可跳过抓取
- 每轮 crawl 写 `manifest.json` 统计

### 4.5 账号管理

文件：`accounts.py`, `biz_search.py`, `account_service.py`

- 配置：`config/accounts.yaml`（`nickname` + `fakeid`）
- CLI：`python -m worker accounts resolve|add|search`

---

## 5. 三条关键数据路径

### 5.1 常规定期采集（每周）

```
week_range("last") → 对每个号 fetch_week → process → upsert
归档目录：data/archive/{当前week_id}/
```

### 5.2 首次订阅回填（每号一次）

```
year_range() → 180天 → fetch_range(max_pages=80)
归档目录：按每篇文章 publish_time 归入对应 week_id 文件夹
标记：mark_year_backfill_done(fakeid)
```

### 5.3 本地缓存命中（跳过网络）

条件（同时满足）：
- 非 `--force-content`
- `is_detailed_archive(txt_path)`：`content_fetched=true` 且有效正文 ≥ 200 字

行为：从 txt `load_cached_content()`，不写 txt；仍 `upsert_articles()` 入库

---

## 6. 与 Insight 的衔接

| crawl 产出 | insight 消费方 |
|------------|----------------|
| `articles.plain_content` | Phase A 摘要输入 |
| `articles.content_embedding` | insight embed 补全（非 crawl 写入） |
| `data/archive/{week_id}/` | selector DB 失败时 fallback |
| `crawl_runs.stats` | 运维监控、正文覆盖率 |

详见 [`../../insight/docs/11-integration/crawl-handoff.md`](../../insight/docs/11-integration/crawl-handoff.md)

---

## 7. 失败与降级

| 场景 | 行为 |
|------|------|
| 无 token | 等待至 `--wait-timeout` 或 `--no-wait` 直接退出 |
| 单号列表失败 | 记录 error，继续下一号 |
| 正文抓取失败 | `content_fetched=false`，txt 写 digest + 错误原因 |
| 公开页被拦 | 自动 token 回退 |
| DB 不可用 | crawl 仍可写 txt（但当前 service 会 init_db，通常需 PG） |

---

## 8. 扩展阅读

- [02-pipeline.md](02-pipeline.md) — 逐步时序
- [03-content-fetch.md](03-content-fetch.md) — 正文抓取细节
- [04-archive-format.md](04-archive-format.md) — txt 格式
- [05-database.md](05-database.md) — 表结构
- [08-auth-prerequisite.md](08-auth-prerequisite.md) — 登录流程
