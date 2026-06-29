# 路线图与已知问题

---

## 实现阶段

### Phase 0（已完成）
- crawl + archive + PostgreSQL 基础
- insight CLI skeleton + selector DB + fallback

### Phase 1（已完成）
- Phase A/B/C 完整流水线
- Ollama bge-m3 embedding（1024 维）
- Rolling Themes（themes 表）
- validator（链接 → aid 后验校验）

### Phase 1.5（已完成）
- ✅ 账号自动画像（profile.py）
- ✅ accounts.yaml 全面 lens 覆盖（8 个号已写入 insight_lens / insight_tags，yaml→DB 同步）
- ✅ 邮件推送 report（`mailer.send_insight_report`，生成后自动发送）

### Phase 2（已完成 → 2026-06 优化）

**篇级 RAG**（详见 [06-rag-retrieval.md](./06-rag-retrieval.md)）

- ✅ 摘要同空间 Top-K（`summary_embedding` ↔ centroid / Primary 向量）
- ✅ 逐篇 Primary 检索 + `theme_tags` 交集过滤
- ✅ 阈值 0.58、每主题 ≤4 篇、总量 ≤30（宁缺毋滥）
- ✅ `report.meta.json` → `rag_retrieval_log` 可观测
- ✅ Phase C Prompt 注入 `{context_articles_text}` 历史文章片段
- ✅ 动态 Context 窗口：`velocity_hint` → fast=60 / medium=180 / slow=365 天

**其他**
- claims 全局矛盾检测（Phase A 已抽取但未做跨篇校验）
- Agent 追问接口（报告后的交互式问答）

### Phase 3（长期规划）
- embedding 质量 A/B 测试（bge-m3 vs OpenAI text-embedding-3-small）
- 多读者 persona 报告变体（同一周，不同 reader_focus）
- 实时号外模式（不只 weekly，热点触发）

---

## 与原设计文档的偏差

| 项目 | 原设计 | 实际实现 |
|------|--------|----------|
| Embedding 维度 | OpenAI 1536 | Ollama bge-m3 **1024** |
| 模型 ID | Claude Haiku/Sonnet | 智谱 glm-4-flash/plus（OpenAI 兼容） |
| article_summaries.aid | REFERENCES articles(aid) | 无 FK（partial unique index 不支持） |
| Phase 2 RAG | 规划实现 | ✅ summary_embedding 同空间 Top-K + 逐篇检索 + 标签过滤 |
| 动态 Context 窗口 | velocity → 60/180 天 | ✅ themes + RAG 均按 velocity_hint |

---

## 已知隐患

### 1. Embed 与 Phase B 的时序陷阱

`run_embed_all()` 在 Phase A **之前**运行。Phase A 新生成的摘要，若 inline embed 失败（或 `--skip-embed`），Phase B 拿到 `summary_embedding = NULL`，该篇降级为"各自成簇"，**不报错**但影响聚类质量。

**缓解**：正常路径 Phase A 有 `embed_single_summary()` inline；独立 `insight embed` 可补全。

### 2. Phase C max_input_tokens 截断

Primary 篇数多时（50+ 篇），`_summaries_table()` 按 `max_input_tokens` 截断摘要表，被截断的文章不参与 Phase C 叙述（但仍在 Phase B 主题中）。日志会打 WARNING。

### 3. quality_score 门槛偏低

当前过滤 `< 0.4`，但 Prompt 说 0.6 = 正常。0.4–0.6 之间的低质摘要（转发/短讯）仍参与聚类，可能稀释主题。可考虑升至 0.5，或在 Phase B 输入时加权而非硬过滤。

### 4. Profile 写 yaml 可能丢失手工注释

`tags.py` 重写 `accounts.yaml` 的方式可能删掉手工写的 `#` 注释。临时解决：对重要账号设 `insight_profile_locked: true`。

### 5. Phase B instructor 稳定性

`instructor` 对部分厂商 JSON 输出不稳定（非标准 function calling）。失败时自动降为规则兜底（topic_tags 频次分组），报告主题质量下降但不崩溃。可查 WARNING 日志确认。

### 6. 摘要并发可能触发 rate limit

`max_concurrency=5` + 摘要生成约 1–2s/篇 → ~3–5 req/s。GLM-4-Flash 限速约 10–20 req/s，通常不触发。若遇 429 错误，在 `insight.yaml` 降低 `phase_a.max_concurrency` 至 2–3。

---

## 审查清单

- [ ] `insight_repo.py` SQL 字段与 `migrate.py` DDL 完全一致
- [ ] txt fallback 的 aid 是否与 DB 中一致（frontmatter 可能为空）
- [ ] validator 对无法反查的 link 处理是否过松（只 warn，不阻断）
- [ ] Phase B ThemeClusterList JSON 输出是否所有厂商都稳定
- [ ] profile 写 yaml 是否丢失已有手工注释
