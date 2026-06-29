# Phase 2 篇级 RAG 检索方案

> 2026-06 优化版：解决 history_comparison 弱配对、凑数注入等问题。

---

## 背景与问题

旧版 RAG 存在三个结构性问题：

| 问题 | 旧行为 | 后果 |
|------|--------|------|
| **跨空间检索** | centroid（`summary_embedding` 均值）查 `content_embedding` | 摘要语义 vs 正文语义偏移，误召「沾边」文章 |
| **阈值过低** | `rag_min_similarity: 0.45`，每主题 `limit: 6` | 14 个主题全部凑满 6 篇，弱相关也进 Prompt |
| **宽主题单向量** | 仅 1 个 centroid 代表多篇差异很大的 Primary | 均值向量模糊，检索发散 |

---

## 当前方案（三件套）

### 1. Hybrid 双路检索（summary 优先 + content 回退）

历史库现状：**仅本周 Primary 有 `summary_embedding`**；更早文章通常只有 `content_embedding`（embed 已跑、Phase A 未跑）。纯 summary 检索会导致 **0 命中**。

| 模式 | 行为 |
|------|------|
| **`hybrid`（默认）** | 同时查 `summary_embedding` + `content_embedding`，按 aid 取最高 similarity |
| `auto` | 先 summary，无结果再 content |
| `summary` | 仅摘要同空间（需历史 Phase A 回填） |
| `content` | 仅正文向量（旧行为） |

- **summary 路**：centroid / Primary → `article_summaries.summary_embedding`，阈值 `rag_min_similarity`（0.58）
- **content 路**：同一查询向量 → `articles.content_embedding`，阈值 `rag_content_min_similarity`（0.50，跨空间略低）

随历史摘要逐步回填，summary 路命中会自然增多，content 路仍作兜底。

### 2. 阈值与配额（宁缺毋滥）

配置见 `worker/config/insight.yaml` → `phase_c`：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `rag_min_similarity` | **0.58** | summary 空间 cosine 下限 |
| `rag_content_min_similarity` | **0.50** | content 回退阈值 |
| `rag_embedding_mode` | **hybrid** | 双路检索模式 |
| `rag_per_theme_limit` | **4** | 每主题最终注入篇数上限 |
| `rag_per_article_limit` | **2** | 主题内每篇 Primary 单独检索 Top-K |
| `rag_total_limit` | **30** | 全报告注入历史文总数上限 |
| `rag_tag_filter` | **true** | 启用标签交集过滤 |

允许某主题 RAG 命中 0–1 篇；history_comparison 少写优于硬凑。

### 3. 逐篇检索 + 标签过滤

**每个主题内**（`retriever.get_rag_context_for_themes`）：

```
1. centroid → summary_embedding Top-K（K = rag_per_theme_limit）
2. 对 theme.aids 中每篇 Primary：
     该文 summary_embedding → Top-K（K = rag_per_article_limit）
3. 按 aid 合并，保留最高 similarity
4. 标签过滤：historical.topic_tags ∩ theme.theme_tags ≠ ∅
5. 按 similarity 排序，取 rag_per_theme_limit 篇
6. 全局去重 + total_limit 截断
```

**标签过滤规则**（`topic_tags_overlap`）：

- 主题无 `theme_tags` → 不过滤
- 历史文无 `topic_tags` → **放行**（常见于仅有正文向量的历史库）
- 双方都有标签且无交集 → 剔除

---

## 数据流

```
Phase B centroid + Primary summary_embedding
        │
        ▼ 同空间 ANN（article_summaries.summary_embedding）
   候选池（centroid + 逐篇合并）
        │
        ▼ topic_tags 交集过滤 + similarity 排序
   每主题 ≤ rag_per_theme_limit
        │
        ▼ 全局 dedupe + rag_total_limit
   Phase C Prompt 白名单 + report.meta.json rag_retrieval_log
```

---

## 可观测性

`report.meta.json` 新增字段：

```json
"rag_retrieval_log": [
  {
    "aid": "...",
    "theme": "心理学与行为决策",
    "similarity": 0.6123,
    "source": "primary:2247520xxx_1",
    "title": "...",
    "topic_tags": ["行为经济学", "决策"]
  }
]
```

- `source`: `centroid` 或 `primary:{本周aid}`
- 验收：`rag_theme_counts` 不应再出现「全部主题同一数字」；多数 similarity ≥ 0.58

---

## 源码索引

| 模块 | 职责 |
|------|------|
| `db/insight_repo.py` | summary_embedding SQL |
| `insight/retriever.py` | 合并检索、标签过滤、格式化 |
| `insight/service.py` | 编排 + 写入 meta |
| `config/insight.yaml` | 阈值与开关 |

---

## 后续可选优化（未实现）

- Hybrid：`summary_embedding` + `content_embedding` 加权重排
- MMR：同主题内结果多样性
- Cross-encoder 精排（bge-reranker）
- Phase C Prompt：禁止重复 `past_aid`、注入 similarity 分数
