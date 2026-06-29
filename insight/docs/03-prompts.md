# Prompt 设计

> 完整模板源码：`worker/insight/prompts.py`

---

## 设计原则

1. **结构优先**：Phase A/B/Profile 用 `instructor + Pydantic` 强制 JSON 输出，不依赖 LLM 格式规范
2. **视角注入**：`account_lens` + `_LENS_GUIDE` 告诉模型"从什么角度读这篇文章"
3. **禁止幻觉**：Phase C 明确禁止编造材料中未出现的事实，validator 事后校验链接
4. **给模型足够约束**：字数范围、分值档位、输出 JSON 示例，减少格式漂移

---

## Phase A Prompt（单篇摘要）

**调用方式**：`structured_completion(prompt, SummaryOutput, model=phase_a_model)`  
**模型**：`glm-4-flash`（默认，快 + 便宜）

### 占位符

| 占位符 | 来源 |
|--------|------|
| `{title}` | articles.title |
| `{nickname}` | accounts.nickname |
| `{account_lens}` | accounts.insight_lens（冷启动为 `general`） |
| `{plain_content}` | articles.plain_content[:phase_a_content_truncate] |
| `{truncate}` | phase_a_content_truncate（默认 4000） |
| `{lens_guide}` | 模块内 `_LENS_GUIDE` 常量，lens 枚举含义说明 |

> `reader_focus` **不**注入 Phase A，只在 Phase C 注入。

### lens 说明（`_LENS_GUIDE`）

```
- industry  : 产业/投融资/商业化，关注产业链、资本、时间表
- interview : 访谈/播客/人物观点，关注引用观点、范式转变
- science   : 科普/学术/方法论，关注证据边界、科学共识
- business  : 商业/消费/宏观，关注数据、案例、商业逻辑
- general   : 未分类，中性摘要
```

### 输出 Schema

```python
class SummaryOutput(BaseModel):
    summary      : str                              # 150-300 字
    topic_tags   : List[str]                        # 3-5 个中文名词短语
    claims       : List[str]                        # ≤3 条可核验事实（非观点）
    sentiment    : Literal["neutral","bullish","bearish"]
    quality_score: float                            # 0.0-1.0
```

### 调优方向

- 公号大量英文内容 → 可在 `{lens_guide}` 后加说明"本号为英文内容，tags 可用英文"
- 短正文（<500 字）quality 普遍 0.3–0.4 → 可在 phase_a_content_truncate 后加"字数过少时不强求 claims"

---

## Phase B Prompt（主题聚类整合）

**调用方式**：`structured_completion(prompt, ThemeClusterList, model=phase_b_model)`  
**模型**：`glm-4-flash`

### 占位符

| 占位符 | 内容 |
|--------|------|
| `{n}` | 本周摘要总篇数 |
| `{theme_min}` / `{theme_max}` | 目标主题数范围（8–15） |
| `{candidate_clusters_json}` | 每簇：cluster_id, aids, 高频 topic_tags, 摘要前80字 |
| `{rolling_themes_compact}` | 近 6 个月 active themes 压缩版（供命名参考） |

### 输出 Schema

```python
class ThemeClusterList(BaseModel):
    themes: List[ThemeClusterOutput]

class ThemeClusterOutput(BaseModel):
    theme_key         : str               # kebab-case 英文 ID
    theme             : str               # 中文主题名 4-10 字
    theme_tags        : List[str]         # 3-6 个标签
    aids              : List[str]         # 该主题下所有 aid（必须完整）
    source_mix        : Dict[str, int]    # lens → 篇数
    theme_summary     : str               # 150-200 字
    novelty_hint      : str               # 相对历史新在哪，无则"延续讨论"
    narrative_chain_id: Optional[str]     # 延续已有 theme_key 或 null
    confidence        : float             # 0-1
    velocity_hint     : Literal["fast","medium","slow"]
```

### 关键约束

- `aids` 必须完整收录该主题下所有候选簇的 aid，不要遗漏
- `source_mix` 的 key 来自文章的 `account_lens` 字段
- 单篇归入最相近主题，不新建"其他"兜底类

---

## Phase C Prompt（洞见报告生成）

**调用方式**：`chat_completion(prompt, model=phase_c_model)`（非结构化，自由 Markdown）  
**模型**：`glm-4-plus`（质量优先）

### 占位符

| 占位符 | 内容 |
|--------|------|
| `{week_id}` | 如 `2026-W25` |
| `{reader_focus}` | insight.yaml reader_focus 列表，逗号连接 |
| `{context_themes_json}` | retriever 格式化的历史主题 timeline |
| `{primary_themes_json}` | Phase B ThemeClusterOutput 列表 JSON |
| `{summaries_table}` | Markdown 表格行：aid \| 公众号 \| lens \| 标题 \| 摘要 |

### 写作要求（Prompt 内指导）

- 每条洞见必须有具体文章引用：`[标题](link)`
- 禁止编造材料中未出现的事实
- 字数范围：1500–3000 字
- lens 视角用于丰富分析维度，不要机械拆分每个主题

### 报告章节模板

| 章节 | 要求 |
|------|------|
| Executive Summary | 3-5 条 bullet，以 `·` 开头，只写最重要信号 |
| 本周主线 | 每主题：来源构成、置信度、发生了什么、为什么重要、与近6月关系、代表文章 |
| 趋势对照 | 表格：主题 \| 6个月前 \| 近期 \| 本周 \| 判断 |
| 分歧与噪声 | 不同来源相反观点；无则写"本周各来源观点基本一致" |
| 值得跟进 | 2-4 条下周应关注信号 |

---

## Profile Prompt（账号画像）

**调用方式**：`structured_completion(prompt, AccountProfileOutput, model=profile_model)`  
**模型**：`glm-4-plus`

### 占位符

| 占位符 | 内容 |
|--------|------|
| `{nickname}` | 公众号名 |
| `{fakeid}` | 公众号 ID |
| `{summaries_compact}` | 近 6 个月摘要样本（title + summary + topic_tags 拼接） |

### 输出 Schema

```python
class AccountProfileOutput(BaseModel):
    fakeid       : str
    nickname     : str
    insight_lens : Literal["industry","interview","science","business","general"]
    insight_tags : List[str]      # 3-6 个高频 topic 的上位概念
    confidence   : float          # < 0.6 时强制选 general
    evidence     : List[str]      # 2-3 条推断依据（如"80% 文章含融资数据"）
    sample_aids  : List[str]      # 2-3 个代表性 aid
```

### 关键规则

- 推断依据是**文体与话题分布**，不是公众号名称
- `confidence < 0.6` → 强制 `insight_lens = "general"`，避免误分类
- 样本不足（< 10 条摘要）→ 不触发画像

---

## LLM 调用封装（llm.py）

```python
# 结构化输出（A/B/Profile）
result: SummaryOutput = await structured_completion(
    prompt, SummaryOutput,
    model=settings.phase_a_model,
    max_tokens=1024,
    settings=settings,
)

# 自由文本（Phase C）
text, usage = await chat_completion(
    prompt,
    model=settings.phase_c_model,
    max_tokens=8192,
    settings=settings,
)
```

`structured_completion` 使用 `instructor.from_openai(client, mode=instructor.Mode.JSON)`，自动处理 JSON 解析失败重试。
