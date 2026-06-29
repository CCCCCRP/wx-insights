# 正文抓取策略

> 代码：`content_fetcher.py`（编排）、`article_content.py`（公开页解析）

## 双路径架构

```
article.link (/s/...)
        │
        ▼
┌───────────────────┐
│ _fetch_public()   │  无 token，模拟浏览器 UA
│ parse_article_html│  正则提取 #js_content
└─────────┬─────────┘
          │ 成功且 content_len > 0
          ▼
     content_source = "public"
          │
          │ 失败：blocked / 空正文 / 网络错误
          ▼
┌───────────────────┐
│ _fetch_with_token │  token + cookie
│ content_processor │  凭证服务目录内复杂排版解析
└─────────┬─────────┘
          ▼
     content_source = "token"
```

## 公开页解析（article_content.py）

检测拦截：

```python
blocked = "环境异常" in html or "完成验证后即可继续访问" in html
```

正文提取优先级：

1. `var msg_title` / `og:title` / `<title>`
2. `#js_content` 或 `.rich_media_content` 区块
3. HTML → 纯文本：去标签、`<br>`/`<p>` 换行、合并空行

## ArticlePageFetcher.enrich()

写入 article dict 字段：

| 字段 | 说明 |
|------|------|
| `plain_content` | 纯文本正文 |
| `content` | 同 plain（兼容） |
| `content_fetched` | bool |
| `content_len` | 字符数 |
| `content_source` | `public` / `token` |
| `content_error` | 失败原因：`blocked` / `no link` / `empty content` 等 |

## 速率限制

| 配置 | 默认 | 位置 |
|------|------|------|
| `ARTICLE_FETCH_DELAY` | 1.5s | 每次 enrich 后 sleep |
| `ARTICLE_FETCH_RETRIES` | 2 | 公开/token 重试 |
| `ARTICLE_FETCH_TIMEOUT` | 30s | httpx timeout |

## 本地缓存跳过（与 enrich 无关）

见 [04-archive-format.md](04-archive-format.md)：`is_detailed_archive()` 在 **enrich 之前**判断，命中则不调本文 API。

## 独立调试

```bash
python -m worker.crawl.article_content "https://mp.weixin.qq.com/s/..."
```

## 常见失败

| 现象 | 原因 | 处理 |
|------|------|------|
| `blocked` | 微信环境异常/验证码 | token 回退；仍失败则留 digest |
| `token unavailable` | 无凭证或过期 | 重新 login |
| 正文很短 | 纯图/视频号 | `content_fetched` 可能 true 但 insight quality 低 |
