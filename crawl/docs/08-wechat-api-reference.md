# 微信公众平台 API 参考

## 爬虫使用的 API 摘要

| API | 文件 | 用途 |
|-----|------|------|
| `cgi-bin/appmsgpublish` | `fetcher.py` | 文章列表 |
| `cgi-bin/searchbiz` | `biz_search.py` | 搜公众号 fakeid |
| `/s/{id}` 公开页 | `article_content.py` | 无 token 正文 |
| token 正文接口 | `content_fetcher.py` | 复杂排版回退 |

## 外部依赖（`WECHAT_API_DIR`）

凭证服务目录需提供 Python 模块：

- `utils.auth_manager` — 凭证读写
- `utils.content_processor` — token 模式 HTML 解析

crawl **不**启动凭证服务的 HTTP 进程，仅 import 上述模块。
