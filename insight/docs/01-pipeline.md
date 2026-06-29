# 完整流水线技术细节

> 源码入口：`worker/insight/service.py` → `InsightService.run()`

---

## 整体时序（service.py 逐步）

```
 1. InsightSelector.select()
       → (primary: List[ArticleRecord], context: List[ArticleSummaryRecord], stats)

 2. dry_run? → 打印 stats，退出

 3. _pre_flight(stats)
       → 正文覆盖率 < 70% 时打 WARNING

 4. init_db()                      # 幂等建表/索引

 5. run_profile()                  # Phase 1.5（除非 --skip-profile）

 6. run_embed_all()                # 补历史缺失 embedding（除非 --skip-embed）
       ⚠ 此步骤在 Phase A 之前；Phase A 新生成的摘要由 inline embed 补向量

 7. primary_count == 0?
       → generate_short_report(), 写文件，退出

 8. ensure_summaries_for_primary()  # Phase A（asyncio）

 9. 过滤 quality_score < 0.4

10. load_active_themes() + compact_rolling_themes()   # 准备 Phase B 上下文

11. run_clustering()               # Phase B（asyncio）

12. update_rolling_themes()        # 写/合并 themes 表

13. get_context_for_themes()       # pgvector 检索历史 themes timeline

14. generate_report()              # Phase C（asyncio）

15. validate_report()              # 后验：链接 → aid 校验

16. _write_report()                # 写 report.md + meta.json + insights 表
```

---

## Phase A：单篇结构化摘要

**源码**：`insight/summarizer.py`

### 触发条件

对每篇 Primary 文章，满足以下条件才调 LLM：
1. `content_fetched=true`，`plain_content` 非空
2. 无已有摘要，**或** `content_hash` 变化（`md5(plain_content[:4000])`）

### 并发控制

```python
asyncio.Semaphore(settings.phase_a_max_concurrency)  # 默认 5
```

失败单篇 warning + retry（默认 2 次），不阻断整批。

### 输入 → 输出

```
输入：title, plain_content[:4000], account_lens, nickname
输出（SummaryOutput Pydantic）：
  summary      : str          # 150-300 字摘要
  topic_tags   : list[str]    # 3-5 个中文名词短语
  claims       : list[str]    # 至多 3 条可核验事实句（非观点）
  sentiment    : neutral | bullish | bearish
  quality_score: float 0-1    # 模型自评信息价值
```

### quality_score 参考

| 分值 | 含义 |
|------|------|
| 0.1 | 纯广告 / 通知 / 无实质内容 |
| 0.3 | 转发摘编，信息量少 |
| 0.6 | 正常报道，有信息增量 |
| 0.8 | 独立分析 / 原始数据 / 深度观点 |
| 0.9 | 重要一手信息 / 研究级内容 |

`service.py` 在 Phase B 前过滤 `quality_score < 0.4`。

### 持久化

- 优先写 `article_summaries` 表（DB upsert）
- DB 不可用时写 `data/insights/cache/summaries/{aid}.json`
- 写完后可选 `embed_single_summary()`（inline embedding）

---

## Embedding 层

**源码**：`insight/embedder.py`

### 为什么需要

文本无法直接做相似度计算；embedding 把语义映射为 1024 维向量，支撑：
- Phase B 摘要聚类（cosine distance）
- Rolling Themes 新旧匹配（threshold ≥ 0.72）
- Phase C Context 检索（pgvector ANN）
- Phase 2 篇级 RAG（history_comparison 历史对照）

### 两类向量

| 字段 | 表 | 文本来源 | 用途 |
|------|-----|----------|------|
| `summary_embedding` | article_summaries | Phase A 摘要（≤2000 字） | Phase B 聚类 + **Phase 2 RAG** |
| `content_embedding` | articles | 正文 plain_content（≤6000 字） | 预留（Hybrid 精排等） |

### Backend 支持

| backend | 实现方式 | 配置 |
|---------|----------|------|
| `ollama`（默认） | POST `http://localhost:11434/api/embed` | insight.yaml + OLLAMA_BASE_URL |
| `openai` | OpenAI embeddings API | OPENAI_API_KEY |
| `sentence_transformers` | 本地 ST 模型 | 模型名 |

### Ollama 超长处理

```
输入 batch → 400 context length error?
    是，且 len > 1 → 二分拆批递归重试
    是，且 len == 1 → 截断至 75% 重试（Warning 日志），直到 ≤500 字 则报错
```

### 批量处理优化（已实现）

```python
# 整个 embed_all 共用一个 httpx.AsyncClient（Ollama 场景）
async with httpx.AsyncClient(timeout=120.0) as http_client:
    while True:
        n = await embed_summaries_batch(settings, http_client=http_client)
        ...

# 一批多条用 executemany 写 DB，非逐条开连接
insight_repo.update_summary_embeddings_batch([(vec, aid), ...])
```

### Ollama 本地部署

```bash
brew install ollama && brew services start ollama
ollama pull bge-m3

# 验证维度
curl -s http://localhost:11434/api/embed \
  -d '{"model":"bge-m3","input":["测试"]}' \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)['embeddings'][0]))"
# 应输出 1024
```

### 时序注意

`run_embed_all()` 在 Phase A **之前**运行。Phase A 新生成的摘要由 `embed_single_summary()`（inline）补向量。若 inline 失败或使用 `--skip-embed`，该篇 Phase B 降级为「各自成簇」，不报错但影响聚类质量。

---

## Phase B：向量聚类 + LLM 整合

**源码**：`insight/cluster.py`

### 两阶段设计

```
摘要列表（quality_score ≥ 0.4，有 summary_embedding）
    │
    ├─ Step 1: scipy 层次聚类（纯算法，无 LLM）
    │       pdist(embeddings, metric="cosine")
    │       linkage(Z, method="average")
    │       fcluster(Z, t=distance_threshold, criterion="distance")
    │       默认 distance_threshold = 0.35（越大 → 簇越少）
    │       无 embedding 的单篇 → 各自成簇
    │
    └─ Step 2: LLM 整合（structured_completion → ThemeClusterList）
            输入：每簇的 aids + 高频 topic_tags + 摘要前80字
            任务：合并语义重叠的簇，保证最终 8–15 个主题
            兜底：无 API Key 时 → 规则分组（topic_tags 频次）
```

### 输出

```python
ThemeClusterOutput:
  theme_key        : str          # kebab-case，如 "agent-coding-tools"
  theme            : str          # 中文主题名，4-10 字
  theme_tags       : list[str]    # 3-6 个标签
  aids             : list[str]    # 该主题下所有文章 aid
  source_mix       : dict         # lens → 篇数，如 {"industry": 3, "general": 1}
  theme_summary    : str          # 150-200 字描述
  novelty_hint     : str          # 相对历史的新意；无则"延续讨论"
  narrative_chain_id: str | None  # 若延续历史主题则填其 theme_key
  confidence       : float        # 0-1
  velocity_hint    : fast|medium|slow
```

### Centroid 计算

```python
centroid = mean(summary_embedding for article in cluster)
```

用于 Rolling Themes 匹配（相似度 ≥ 0.72 视为同一主题）和 Phase C Context 检索。

### 调参

- 主题太多 → 增大 `cluster_distance_threshold`
- 主题太少 → 减小 threshold
- 中文摘要短、向量噪声大 → 略增大（如 0.4）

---

## Rolling Themes

**源码**：`insight/themes.py`，存储：`themes` 表

### 为什么需要

避免每周从零命名"AI Agent"类话题，实现跨周主题追踪和趋势对照。

### update_rolling_themes() 逻辑

```
对每个 Phase B 主题：
    取 centroid embedding
    → find_similar_theme(embedding, threshold=0.72)
    命中（similarity ≥ 0.72）→ append_theme_timeline()  # 合并进已有主题
    未命中            → insert_theme()             # 新建 theme_key
```

### timeline 结构

timeline 是**列表**，每次出现追加一条：

```json
[
  { "week_id": "2026-W24", "status": "active", "article_count": 3, "aids": ["..."] },
  { "week_id": "2026-W25", "status": "active", "article_count": 5, "aids": ["..."] }
]
```

`retriever.py` 取最近 4 条 entry 格式化为 Context Mirror 文本。

### 归档

`archive_after_days`（默认 180）：`last_seen_week` 超期的主题标记 `archived=true`。

---

## Phase C：洞见报告生成

**源码**：`insight/generator.py`，`insight/retriever.py`

### Context 检索（retriever.py）

```python
对每个 Primary 主题的 centroid：
    pgvector ANN 查 themes 表（archived=false，last_seen_week ≥ cutoff）
    取相似度最高的 3 个历史主题的 timeline
合并去重 → 格式化为 Markdown 列表供 Phase C Prompt 使用
```

输出格式：
```
- Agent 编程工具链: 2026-W22:active(3篇) → 2026-W23:active(5篇) → 2026-W25:active(4篇)
```

### Prompt 输入结构

```
Phase C Prompt 包含 4 块：
1. reader_focus        — 读者关注维度（insight.yaml）
2. Context Mirror      — 历史主题 timeline（retriever 输出）
3. Primary Themes JSON — Phase B 输出（ThemeClusterOutput 列表）
4. 摘要清单表格        — aid | 公众号 | lens | 标题 | 摘要（≤120字）
```

### max_input_tokens 截断

`phase_c.max_input_tokens`（默认 12000）控制摘要表最大字符：

```python
max_summary_chars = max(2000, (max_input_tokens - 4000) * 1)
```

超出则截断表格并追加说明行，日志 WARNING。

### 报告结构（LLM 输出 Markdown）

```markdown
# 洞见周报 · 2026-W25

## Executive Summary
· [信号一]
· [信号二]
（3-5 条）

## 本周主线
### [主题名]（#tag1 #tag2）
**来源构成**：...
**置信度**：...
**发生了什么**：...
**为什么重要**：...
**与近 6 个月的关系**：...
**代表文章**：[标题](link)

## 趋势对照（Context Mirror）
| 主题 | 6个月前 | 近期 | 本周 | 判断 |

## 分歧与噪声

## 值得跟进
```

### 后验校验（validator.py）

```python
validate_report(report_md, primary_aids, primary_links) → list[str]
```

1. 正则提取报告内 `mp.weixin.qq.com/s/...` 链接
2. `link → aid` 反查（DB lookup）
3. aid 不属于 Primary → 追加 warning

不阻断发布；warnings 写入 `report.meta.json`，日志前 3 条。

---

## 账号画像（Phase 1.5）

**源码**：`insight/profile.py`

### 触发条件（两者满足）

1. 近 6 个月摘要数 ≥ `profile.min_summaries`（默认 10）
2. 距上次画像超过 `profile.recalibrate_days`（默认 90 天）
3. `insight_profile_locked=true` 的账号**跳过**（保护手工配置）

### 输出

```python
AccountProfileOutput:
  insight_lens : industry | interview | science | business | general
  insight_tags : list[str]   # 3-6 个上位概念
  confidence   : float       # < 0.6 时强制选 general
  evidence     : list[str]   # 2-3 条推断依据
  sample_aids  : list[str]   # 代表性文章 aid
```

### 写回目标

- `accounts` 表：`insight_lens`, `insight_tags`, `insight_profile_confidence`
- `config/accounts.yaml`：同步写入（⚠️ 可能丢失手工注释，见 05-roadmap）

### 两层标签体系

| 层 | 字段 | 变化速度 | 作用 |
|----|------|----------|------|
| L1 | `insight_lens` / `insight_tags` | 慢（账号级，90 天重算） | Phase A Prompt 注入视角 |
| L2 | `topic_tags` | 快（每篇，每周更新） | Phase B 聚类特征 |

Phase B 主路径是 **embedding 向量聚类**，topic_tags 只作命名辅助特征，不是主路径。

---

## 输出路径

```
worker/data/insights/
├── {week_id}/
│   ├── report.md            ← 洞见周报正文
│   └── report.meta.json     ← 元数据（stats, warnings, model）
└── cache/summaries/{aid}.json  ← 无 DB 时的摘要缓存
```

`insights` 表也存一份：`week_id`, `content_md`, `meta` JSONB。
