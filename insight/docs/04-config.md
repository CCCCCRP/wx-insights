# 配置参考

---

## insight.yaml 完整说明

路径：`worker/config/insight.yaml`

```yaml
# 读者关注维度，注入 Phase C Prompt
reader_focus:
  - AI产品化与应用落地
  - 科技公司战略与组织
  - 前沿研究的工程实践

llm:
  # base_url 也可写这里，通常放 .env 的 OPENAI_BASE_URL
  # 智谱: https://open.bigmodel.cn/api/paas/v4/
  # DeepSeek: https://api.deepseek.com

phase_a:
  model: glm-4-flash          # 单篇摘要，便宜快
  max_concurrency: 5           # asyncio Semaphore，防 rate limit
  content_truncate_chars: 4000 # 送入 LLM 的正文上限
  retry: 2                     # 单篇失败重试次数

phase_b:
  cluster_distance_threshold: 0.35  # cosine 距离阈值，越大→簇越少
  target_theme_count_min: 8
  target_theme_count_max: 15
  model: glm-4-flash

phase_c:
  model: glm-4-plus           # 质量优先
  max_input_tokens: 12000     # 摘要表字符上限（超出自动截断）
  context_themes_limit: 10    # Context Mirror 最多主题数

embedding:
  backend: ollama             # ollama | openai | sentence_transformers
  model: bge-m3
  dimensions: 1024
  ollama_base_url: http://localhost:11434
  batch_size: 8               # Ollama 单次请求篇数（超长时自动拆批）
  content_truncate_chars: 6000
  summary_truncate_chars: 2000

rolling_themes:
  similarity_threshold: 0.72  # centroid cosine 相似度 ≥ 此值视为同一主题
  archive_after_days: 180     # inactive 主题归档天数

profile:
  model: glm-4-plus
  min_summaries: 10           # 至少 N 条摘要才做画像
  recalibrate_days: 90        # 距上次画像超过此天数则重做
```

---

## 环境变量（.env）

# LLM 分路由（见 insight.yaml）

```yaml
llm:
  local:
    base_url: http://localhost:11434/v1
    api_key: ollama
  cloud:
    base_url: https://api.deepseek.com

phase_a:
  backend: local          # local | cloud
  model: qwen3:14b
  max_concurrency: 2
  no_think: true          # Qwen3 关闭思考链

phase_c:
  backend: cloud
  model: deepseek-v4-flash
```

```env
# 本地 Ollama（Phase A/B/Profile）
OLLAMA_LLM_BASE_URL=http://localhost:11434/v1
OLLAMA_API_KEY=ollama

# 云端 DeepSeek（Phase C）
DEEPSEEK_API_KEY=your-key
OPENAI_BASE_URL=https://api.deepseek.com

# Embedding（Ollama）
EMBEDDING_BACKEND=ollama
EMBEDDING_MODEL=bge-m3
OLLAMA_BASE_URL=http://localhost:11434
```

配置加载顺序：`.env` → `insight.yaml` → 默认值  
各 Phase 通过 `backend: local|cloud` 选择路由，无需改 Python 代码。

---

## LLM 厂商切换

换厂商只需改两个地方，**无需改 Python 代码**：

**1. `.env`**
```env
OPENAI_API_KEY=换成新厂商 Key
OPENAI_BASE_URL=换成新厂商 BaseURL
```

**2. `insight.yaml`**
```yaml
phase_a:
  model: 换成对应厂商的模型 ID
phase_c:
  model: 换成对应厂商的模型 ID
```

### 常用配置

| 厂商 | BASE_URL | 摘要模型 | 报告模型 |
|------|----------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | `deepseek-chat` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4/` | `glm-4-flash` | `glm-4-plus` |
| OpenAI | （留空，使用默认） | `gpt-4o-mini` | `gpt-4o` |
| Claude（代理） | 代理 OpenAI 兼容端点 | `claude-haiku-3-5` | `claude-sonnet-4-5` |

### 注意

`instructor` 结构化输出（Phase A/B/Profile）需要厂商支持 JSON schema / function calling。
不稳定时可查看 `cluster.py` 规则兜底是否覆盖你的场景。

---

## CLI 命令

```bash
# 生成报告（标准流程）
python -m worker insight --week last
python -m worker insight --week 2026-W25

# 选项
--dry-run        只打印数据选取统计，不调 LLM
--skip-embed     跳过 embedding 补全步骤
--skip-profile   跳过账号画像步骤
--no-email       不发送洞见报告邮件
--email-to ADDR  指定报告收件邮箱（默认 NOTIFY_EMAIL）

# 只补向量（不生成报告）
python -m worker insight embed

# 只做账号画像 / 同步 yaml
python -m worker insight profile
python -m worker insight profile --nickname 宝玉AI
python -m worker insight profile --dry-run
python -m worker insight sync-profiles   # 仅 yaml → DB，不调 LLM
```

### 典型每周流程

```bash
# 周一上午（或 cron）
python -m worker login --email           # 扫码登录
python -m worker crawl --week last       # 爬取上周文章

# 可选：提前补 embedding（让 Ollama 先跑）
python -m worker insight embed

# 生成报告
python -m worker insight --week last
```

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | embed 未配置 / 异常 |

---

## 模块依赖关系

```
cli.py
  └── service.py
        ├── selector.py   ← crawl/week.py, crawl/archive.py, tags.py
        ├── profile.py    ← llm.py
        ├── embedder.py   ← db/insight_repo.py
        ├── summarizer.py ← llm.py, embedder.py, insight_repo.py
        ├── cluster.py    ← llm.py, prompts.py
        ├── themes.py     ← insight_repo.py
        ├── retriever.py  ← insight_repo.py
        ├── generator.py  ← llm.py, prompts.py
        └── validator.py  ← insight_repo.py
```

**外部依赖：**
PostgreSQL + pgvector | Ollama（embedding）| OpenAI-compatible API（LLM）
numpy + scipy（聚类）| instructor + pydantic（结构化输出）| httpx（Ollama HTTP）

**不依赖：** crawl 运行时、wechat token（insight 只读 DB/archive，不抓微信）
