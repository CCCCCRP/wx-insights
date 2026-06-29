# 爬虫配置与环境变量

## 配置文件

| 文件 | 作用 |
|------|------|
| `worker/.env` | 密钥与运行时参数 |
| `worker/env.example` | 模板 |
| `worker/config/accounts.example.yaml` | 订阅公众号列表示例 |
| `worker/config/accounts.yaml` | 本地订阅列表（从 example 复制，不提交 git） |
| `{WECHAT_API_DIR}/data/.credentials.json` | 微信 token/cookie |

## accounts.yaml 示例

```yaml
accounts:
  - nickname: 宝玉AI
    fakeid: MzA5...==
  - nickname: 新号
    # fakeid 留空 → crawl 时 accounts resolve 自动搜索
```

## 环境变量（爬虫相关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `WECHAT_API_DIR` | —（必填） | 微信凭证服务根目录 |
| `DATABASE_URL` | — | PostgreSQL 连接串 |
| `ARTICLE_FETCH_DELAY` | `1.5` | 正文请求间隔（秒） |
| `ARTICLE_FETCH_RETRIES` | `2` | 重试次数 |
| `ARTICLE_FETCH_TIMEOUT` | `30` | HTTP 超时 |
| `ARCHIVE_MIN_CONTENT_LEN` | `200` | 本地缓存最小正文字数 |

## 路径常量（config.py）

| 常量 | 路径 |
|------|------|
| `ARCHIVE_ROOT` | `worker/data/archive/` |
| `CONFIG_DIR` | `worker/config/` |
| `API_DIR` | `WECHAT_API_DIR` 解析后的路径 |

## 账号 CLI

```bash
python -m worker accounts resolve    # 补全 fakeid
python -m worker accounts add "公众号名"
python -m worker accounts search "关键词"
```
