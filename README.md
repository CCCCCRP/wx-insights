# wx-insights

微信公众号自动化采集与 **AI 周报洞见** 流水线。

从订阅公众号抓取文章，经 LLM 摘要、向量聚类与 RAG 增强，生成结构化 Weekly Insights 报告，并支持邮件推送、静态博客归档与远程数据库同步（均可选）。

**延伸阅读**：[架构与实践（博客）](https://cairuipengtobebetter.top/archives/wei-ming-ming-wen-zhang-Z1ldToPd) · [Weekly Insights 示例](https://cairuipengtobebetter.top/insights/)

```
login → crawl → embed → insight (Phase A → B → C) → [email] → [blog] → [db-sync]
```

## 功能

| 模块 | 说明 |
|------|------|
| **Crawl** | 扫码登录、按周拉取文章列表与正文、本地 txt 归档 + PostgreSQL |
| **Insight** | Phase A 结构化摘要 → Phase B 主题聚类 → Phase C 洞见报告（含 RAG 历史对照） |
| **Embed** | 本地 Ollama `bge-m3` 向量（1024 维，pgvector HNSW） |
| **Schedule** | 每周自动提醒 + 周一流水线（`python -m worker schedule`） |
| **Blog**（可选） | 将 HTML 报告 rsync 到 Nginx 静态目录 |
| **DB Sync**（可选） | 本地 PostgreSQL 全量/增量同步到远程服务器 |

## 技术栈

Python 3.11 · PostgreSQL 16 + pgvector · Ollama (bge-m3) · DeepSeek API · httpx · instructor + Pydantic

## 前置依赖

1. **Docker** — 本地 PostgreSQL（`docker compose up -d`）
2. **Ollama** — `ollama pull bge-m3`（Embedding 必须）
3. **DeepSeek API Key** — Phase A/B/C 默认走云端 LLM
4. **微信凭证服务** — 在 `.env` 中设置 `WECHAT_API_DIR` 指向已部署的 token/cookie 管理目录

## 快速开始

> 本仓库为 Python 包 **`worker`**。请克隆到名为 `worker` 的目录，并在**其上一级**执行命令。

```bash
# 目录建议
mkdir -p ~/code/wx-insights && cd ~/code/wx-insights
git clone git@github.com:CCCCCRP/wx-insights.git worker

# 依赖
pip install -r worker/requirements.txt

# 配置（勿提交 git）
cp worker/env.example worker/.env
cp worker/config/accounts.example.yaml worker/config/accounts.yaml
# 编辑 .env：DATABASE_URL、DEEPSEEK_API_KEY、SMTP、WECHAT_API_DIR 等

# 数据库
cd worker && docker compose up -d && cd ..

# 或：一键启停（见下方「启停脚本」）
# worker/scripts/wx-insights.sh start

# 登录 → 采集 → 洞见
python -m worker login --email
python -m worker crawl --week last
python -m worker insight --week last
```

## 常用命令

```bash
# 采集
python -m worker crawl --week last
python -m worker accounts resolve

# 洞见
python -m worker insight --week last
python -m worker insight embed
python -m worker insight profile

# 每周全自动（后台挂起示例）
nohup python -m worker schedule >> worker/data/logs/schedule-daemon.log 2>&1 &

# 推荐：用启停脚本管理 schedule + 数据库
# worker/scripts/wx-insights.sh start
# worker/scripts/wx-insights.sh status
# worker/scripts/wx-insights.sh stop

# 可选：博客 / 远程库
python -m worker insight publish-blog --week last
python -m worker db sync
python -m worker db sync --full
```

## 配置说明

| 文件 | 作用 |
|------|------|
| `.env` | 密钥与运行时（从 `env.example` 复制） |
| `config/accounts.yaml` | 订阅公众号列表（从 `accounts.example.yaml` 复制） |
| `config/insight.yaml` | LLM / RAG / 报告 / 博客开关 |

**无博客、无远程服务器时**：保持 `insight.yaml` 中 `blog.enabled: false`，且不配置 `BLOG_SSH_HOST`。流水线会**自动跳过**博客上传与远程 DB 同步，不影响采集和洞见。

有服务器时在 `.env` 中配置：

```bash
BLOG_ENABLED=true
BLOG_SSH_HOST=your-server-ip
BLOG_SSH_PASSWORD=...
BLOG_BASE_URL=https://your-blog.example.com
```

## 启停脚本

`worker/scripts/wx-insights.sh` 管理 **PostgreSQL（Docker）** 和 **schedule 每周调度守护进程**。

> 在 **`worker` 的上一级目录**执行（与 `python -m worker` 相同）。Ollama 需本机单独启动，脚本只做连通性检查。

```bash
# 启动数据库 + schedule 后台守护
worker/scripts/wx-insights.sh start

# 只看状态（Docker / schedule PID / Ollama）
worker/scripts/wx-insights.sh status

# 跟踪 schedule 日志
worker/scripts/wx-insights.sh logs

# 停止全部（先停 schedule，再停数据库）
worker/scripts/wx-insights.sh stop

# 只启停某一组件
worker/scripts/wx-insights.sh start db
worker/scripts/wx-insights.sh stop schedule
worker/scripts/wx-insights.sh restart schedule
```

| 命令 | 作用 |
|------|------|
| `start [all\|db\|schedule]` | 启动（默认 `all`） |
| `stop [all\|db\|schedule]` | 停止（默认 `all`） |
| `restart` | 重启 |
| `status` | 查看 Docker、schedule PID、Ollama |
| `logs [N]` | `tail -f` schedule 日志（默认 50 行） |

- schedule PID：`worker/data/schedule-daemon.pid`
- schedule 日志：`worker/data/logs/schedule-daemon.log`
- 指定 Python：`WX_INSIGHTS_PYTHON=/path/to/python worker/scripts/wx-insights.sh start`

## 项目结构

```
worker/
├── crawl/          # 公众号采集
├── insight/        # Weekly Insights 流水线
├── auth/ scan/     # 登录与扫码
├── mail/           # SMTP 邮件
├── db/             # PostgreSQL + 远程同步
├── config/         # accounts.yaml, insight.yaml
├── scripts/        # wx-insights.sh 启停脚本
├── tests/
└── docker-compose.yml
```

## 文档

| 模块 | 路径 |
|------|------|
| 爬虫 | [crawl/docs/README.md](crawl/docs/README.md) |
| 洞见 | [insight/docs/README.md](insight/docs/README.md) |

## 隐私与 `.gitignore`

以下文件**不会**进入 git，请本地自行维护：

- `.env`
- `config/accounts.yaml`
- `data/`（日志、归档、报告、同步水位）
- `docs/`（仓库根目录个人笔记；`crawl/docs/`、`insight/docs/` 会正常入库）

## License

MIT（可按需修改）
