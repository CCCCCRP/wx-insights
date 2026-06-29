# 爬虫模块文档（Crawl）

> 代码目录：`worker/crawl/`  
> 入口命令：`python -m worker crawl`

## 文档树（总 → 分）

```
crawl/docs/
├── README.md                    ← 你在这里
├── 01-design-overview.md        ★ 整体设计（必读）
├── 02-pipeline.md               运行时流水线逐步拆解
├── 03-content-fetch.md          正文抓取：公开页 vs token 回退
├── 04-archive-format.md         本地 txt 归档格式
├── 05-database.md               PostgreSQL 表与 upsert 逻辑
├── 06-config-env.md             环境变量与 accounts.yaml
├── 07-cli-commands.md           CLI 参数说明
├── 08-auth-prerequisite.md      登录/token 前置条件
└── 08-wechat-api-reference.md   → 跳转 API 参考
```

## 一分钟理解

```
login（扫码）→ crawl（拉列表 + 抓正文）→ data/archive/（txt）+ PostgreSQL（articles）
                                                      ↓
                                              insight 模块消费
```

## 源码文件对照

| 文件 | 文档 |
|------|------|
| `service.py` | [01-design-overview.md](01-design-overview.md)、[02-pipeline.md](02-pipeline.md) |
| `fetcher.py` | [02-pipeline.md](02-pipeline.md) § 列表拉取 |
| `content_fetcher.py` | [03-content-fetch.md](03-content-fetch.md) |
| `article_content.py` | [03-content-fetch.md](03-content-fetch.md) § 公开页解析 |
| `archive.py` | [04-archive-format.md](04-archive-format.md) |
| `week.py` | [02-pipeline.md](02-pipeline.md) § 时间窗 |
| `accounts.py` / `biz_search.py` | [06-config-env.md](06-config-env.md) |
| `../db/repo.py` | [05-database.md](05-database.md) |

## 相关模块（不在 crawl/ 内）

| 模块 | 路径 | 与爬虫关系 |
|------|------|------------|
| 登录 | `worker/auth/`、`worker/scan/` | crawl 前必须有 token |
| 邮件 | `worker/mail/` | 登录二维码邮件 |
| 微信凭证服务 | `WECHAT_API_DIR`（见 `.env`） | 凭证持久化、token 正文解析 |
