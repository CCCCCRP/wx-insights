# Weekly Insights — 文档导览

> 代码：`worker/insight/` | 入口：`python -m worker insight`

## 三句话理解

1. **Primary（上周文章）** 经 Phase A 生成结构化摘要 → embed → Phase B 聚成 8–15 个主题
2. **Context（近 180 天摘要）** 经 `themes` 表 + 向量检索，作为趋势对照镜
3. **Phase C** 把主题 + 对照喂给 LLM，生成 1500–3000 字 Markdown 洞见周报，validator 校验溯源

## 文档索引

| 文档 | 内容 |
|------|------|
| [01-pipeline.md](01-pipeline.md) | **主文档**：完整流水线技术细节，从正文到周报的每一步 |
| [02-data-layer.md](02-data-layer.md) | 数据库 Schema、Primary/Context 时间窗、数据选取、Archive Fallback |
| [03-prompts.md](03-prompts.md) | 四个 Prompt 的设计意图与输入输出 |
| [04-config.md](04-config.md) | insight.yaml、环境变量、LLM 厂商切换、CLI 命令 |
| [05-roadmap.md](05-roadmap.md) | 已知偏差、待完善、Phase 2 规划 |
| [06-rag-retrieval.md](06-rag-retrieval.md) | **RAG 检索方案**：同空间检索、逐篇+标签过滤、配置与验收 |

## 推荐阅读顺序

第一次了解 → `README` → `01-pipeline` → `02-data-layer`  
调 Prompt → `03-prompts`  
换模型/配置 → `04-config`  
查问题/规划 → `05-roadmap`  
调 RAG / history_comparison → `06-rag-retrieval`

## 快速上手

```bash
# 每周标准流程
python -m worker crawl --week last         # 1. 爬取上周文章
python -m worker insight embed             # 2. 补全 embedding（可选先做）
python -m worker insight --week last       # 3. 生成洞见报告

# 调试
python -m worker insight --week last --dry-run        # 只看数据选取统计
python -m worker insight --week last --skip-embed     # 跳过 embed
python -m worker insight --week last --skip-profile   # 跳过账号画像

# 独立子命令
python -m worker insight embed                        # 只补向量
python -m worker insight profile                      # 只做账号画像
python -m worker insight profile --nickname 宝玉AI    # 指定账号
```

## 实现状态

| 能力 | 状态 |
|------|------|
| Phase A 单篇摘要 | ✅ |
| Embedding（Ollama bge-m3 1024 维） | ✅ |
| Phase B 向量聚类 + LLM 整合 | ✅ |
| Rolling Themes | ✅ |
| Phase C 洞见报告 | ✅ |
| 后验校验（validator） | ✅ |
| 账号画像（Phase 1.5） | ✅ |
| Phase 2 篇级 RAG（content_embedding） | ✅ 整篇 Top-K + Phase C 注入 |
| 邮件推送 | ✅ `mailer.send_insight_report` |
| 动态 Context 窗口（velocity） | ⏸ 见 05-roadmap |

## 术语表

| 术语 | 含义 |
|------|------|
| `week_id` | ISO 自然周，如 `2026-W25` |
| **Primary** | 上一自然周内的文章，洞见主体 |
| **Context** | Primary 之前 180 天的历史摘要，趋势对照 |
| `aid` | 微信文章 ID，如 `2650019993_1` |
| `fakeid` | 公众号 ID |
| **lens (L1)** | 账号级解读视角：`industry` / `interview` / `science` / `business` / `general` |
| **topic_tags (L2)** | 单篇内容标签，Phase A 输出，每周变化 |
| **Rolling Themes** | 跨周持久主题，存 `themes` 表 |
| **centroid** | 主题簇内向量均值，用于相似度匹配 |
| `quality_score` | Phase A 自评信息价值 0–1 |
| `content_hash` | `md5(plain_content[:4000])`，检测正文变更 |
| **HNSW** | pgvector 向量近似最近邻索引 |
| **bge-m3** | 本地 Ollama embedding 模型，1024 维 |
