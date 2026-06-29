# 本地归档格式

> 代码：`worker/crawl/archive.py`  
> 根目录：`worker/data/archive/`

## 目录结构

```
data/archive/
  {week_id}/                    # 如 2026-W25
    manifest.json
    {nickname}/
      {YYYYMMDD}_{title}.txt
```

### 文件命名规则

```python
# article_txt_path()
date_str = publish_time → %Y%m%d   # 无时区：Asia/Shanghai +8
fname = f"{date_str}_{safe_title}.txt"  # title 去特殊字符，最长 60 字
```

## manifest.json

```json
{
  "week_id": "2026-W25",
  "start_ts": 1750262400,
  "end_ts": 1750867199,
  "start": "2025-06-19T00:00:00+08:00",
  "end": "2025-06-25T23:59:59+08:00",
  "crawled_at": "2026-06-26T22:00:00+08:00",
  "accounts": [{"fakeid": "...", "nickname": "..."}],
  "stats": {
    "by_account": {"返朴": 5},
    "total_saved": 42,
    "content_fetched": 40,
    "content_failed": 2,
    "content_public": 30,
    "content_token": 8,
    "content_cached": 2,
    "db_backfill": 1,
    "year_backfill_accounts": 0
  }
}
```

## 单篇 txt 格式

```yaml
---
title: 文章标题
nickname: 公众号名
fakeid: xxx
aid: 2650019993_1          # 若有
publish_time: 1781569061
link: https://mp.weixin.qq.com/s/...
digest: 摘要
author: 作者
cover: 封面 URL
content_fetched: true
content_len: 3500
content_source: public     # public | token | local
crawled_at: 2026-06-26T22:00:00+08:00
---

正文纯文本...

```

### 未抓到正文时

body 为 `digest`；末尾附加：

```
# 正文未抓取: blocked
原文: https://...
```

## 缓存判定 `is_detailed_archive()`

全部满足才跳过网页抓取：

1. 文件存在
2. frontmatter `content_fetched: true`
3. `max(content_len, len(body)) >= ARCHIVE_MIN_CONTENT_LEN`（默认 200）

## parse_article_txt()

Insight selector 在无 DB 时也用此函数读 frontmatter + body。

## 与 DB 的关系

| 场景 | txt | DB |
|------|-----|-----|
| 正常 crawl | 写/跳过 | upsert |
| 回填按发布周 | 写入对应 week 目录 | upsert |
| txt 有、DB 无 | 不 rewrite | upsert（db_backfill 计数） |
