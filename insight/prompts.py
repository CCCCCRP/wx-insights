"""Insight Prompt 模板。"""
from __future__ import annotations

# account_lens 枚举说明（供 Phase A prompt 引用）
_LENS_GUIDE = """account_lens 取值含义：
- industry：产业 / 投融资 / 商业化，关注产业链、资本、时间表
- interview：访谈 / 播客 / 人物观点，关注引用观点、范式转变
- science：科普 / 学术 / 方法论，关注证据边界、科学共识
- business：商业 / 消费 / 宏观，关注数据、案例、商业逻辑
- general：未分类，中性摘要"""


PHASE_A_PROMPT = """你是科技/商业内容编辑，擅长中文阅读理解。

## 文章元数据
- 来源公众号：{nickname}（视角类型：{account_lens}）
- 标题：{title}

## 视角类型说明
{lens_guide}

## 正文（最多 {truncate} 字）
{plain_content}

## 任务
请对以上文章输出结构化摘要。要求：

**summary**（150-300 字）
- 提炼文章核心观点和关键事实
- 不复述标题，不扩展原文未提及的内容
- 写法贴合 account_lens 视角（如 industry 侧重产业链叙述，interview 侧重引用观点）

**topic_tags**（3-5 个，中文名词短语，如「具身智能」「大模型融资」）
- 反映本文核心话题，不使用通用词（如「AI」「科技」）

**claims**（至多 3 条可核验事实句，不要观点句）
- 正确示例：「Anthropic 2026 年 Q1 ARR 达 19 亿美元」
- 错误示例：「AI 将改变未来工作方式」（观点，不可核验）
- 无事实数据时返回空列表

**sentiment**（对文章所描述趋势的整体情绪倾向）
- bullish：看好 / 积极 / 技术进步明显
- bearish：看空 / 担忧 / 有实质风险
- neutral：中立 / 描述性 / 无明显倾向

**quality_score**（0.0–1.0，模型自评本文信息价值）
- 0.1：纯广告 / 无实质内容 / 通知类
- 0.3：转发摘编 / 信息量少
- 0.6：正常报道，有信息增量
- 0.8：有独立分析 / 原始数据 / 深度观点
- 0.9：重要一手信息 / 产业洞见 / 研究级内容
"""

PHASE_B_PROMPT = """你是科技趋势分析师。以下是本周 {n} 篇文章基于向量相似度的初步聚类结果。
请将语义重叠的簇合并，输出最终 {theme_min}–{theme_max} 个主题，每个主题附带摘要。

## 候选聚类（每簇包含：编号、aids、高频标签、摘要片段）
{candidate_clusters_json}

## 近 6 个月 Rolling Themes（仅供命名参考，不限制新主题出现）
{rolling_themes_compact}

## 输出要求
返回一个 JSON 对象，格式如下：
{{
  "themes": [
    {{
      "theme_key": "kebab-case 英文键，如 agent-coding-tools",
      "theme": "主题名（4-10 字中文，如「Agent 编程工具链」）",
      "theme_tags": ["3-6 个中文标签"],
      "aids": ["该主题下所有文章的 aid，来自候选聚类"],
      "source_mix": {{"lens类型": 篇数, ...}},
      "theme_summary": "150-200 字，描述本周该话题核心内容",
      "novelty_hint": "相对 rolling themes，本周新在哪里；无新意则填「延续讨论」",
      "narrative_chain_id": "若延续 rolling themes 某主题则填其 theme_key，否则填 null",
      "confidence": 0.0到1.0之间的数字（单来源=0.5，多来源且标签一致=0.9）,
      "velocity_hint": "fast 或 medium 或 slow"
    }}
  ]
}}

注意：
- aids 必须完整收录该主题下候选聚类中的所有 aid，不要遗漏
- source_mix 中的 key 来自文章的 account_lens 字段
- 单篇文章归入最相近主题，不新建「其他」类
"""

PHASE_C_PROMPT = """你是科技/商业趋势分析师。基于本周材料，输出**结构化 JSON**（不是 Markdown）。

## 核心规则（严格执行）
- 本报告周期：**{report_week_id}**（下方摘要清单中 week 列均为本周 Primary 文章）
- `details` 每条必须有 `aid`（本周清单）；`history_comparison` 必须同时有 `past_aid`（RAG 历史清单）和 `aid`（本周清单）
- 每条文字字段禁止写 URL；日期由代码根据 aid 对应文章的 publish_date 自动展示，**不要在文字里写 W16 或「6个月前」**
- 禁止编造 aid、事实、数据
- **时间表述规则（重要）**：
  - 禁止使用「6个月前」「数月前」「近期」等模糊时间
  - past_part / this_week_part **不要写日期或 week_id**（代码会按 past_aid / aid 的 publish_date 自动显示）
  - Context Mirror 中的 week_id 仅用于 insights 里的频次对比（如 W17 每周 1 篇 → 本周 5 篇）
  - `history_comparison` 的 past_part / this_week_part 只写事实；**past_aid 与 aid 分别指向过去与本周各一篇文章**
- 读者偏好：{reader_focus}
- RAG 历史文章（下方表格含 aid）仅供 `history_comparison.past_aid`，不能用作本周 aid

## 历史对比窗口（按主题 velocity_hint，单位：天）
{velocity_window_note}

## 各主题 RAG 历史文章命中数（决定 history_comparison 写几条，禁止凑数）
{rag_theme_counts_note}

## 主题时间线（Context Mirror，按 week_id 量化）
{context_themes_json}

## 历史相关文章（RAG，past_aid 白名单，勿用作本周 aid）
{context_articles_text}

## RAG 历史文章清单（history_comparison.past_aid 只能使用下表 aid）
| aid | week | publish_date | 公众号 | 标题 | 摘要 |
|---|---|---|---|---|---|
{history_articles_table}

## 本周主题簇（Phase B 聚类结果）
{primary_themes_json}

## 本周文章摘要清单（aid 白名单，只能使用这里的 aid）
| aid | week | publish_date | 公众号 | 标题 | 摘要 |
|---|---|---|---|---|---|
{summaries_table}

## 输出 JSON 结构（严格按此格式）

{{
  "themes": [
    {{
      "theme": "主题名 4-10 字",
      "theme_tags": ["标签1", "标签2"],
      "brief_summary": "一句话总结，20-40 字，概括本主题本周最核心信号，不含 URL",
      "details": [
        {{"statement": "具体事实或洞见，一条只讲一件事", "aid": "对应文章 aid"}},
        ...（2-8条，每条必须有 aid，不能合并成长段）
      ],
      "history_comparison": [
        {{
          "past_part": "过去侧事实（对应 past_aid 那篇文章）",
          "past_aid": "RAG 历史清单中的 aid",
          "this_week_part": "本周侧事实（对应 aid 那篇文章）",
          "aid": "本周摘要清单中的 aid"
        }},
        ...（1-6 条，条数见「RAG 历史文章命中数」；素材不足可少写，禁止凑数）
      ],
      "insights": [
        "启示1：结合 Context Mirror 频次（如 W17 每周1篇 → {report_week_id} 本周5篇）给出前瞻判断",
        "启示2：指出当前信号对读者的实际意义，或下一步最值得关注的风险/机会",
        ...（2-4条纯文字，不含 URL，不含 aid；这是分析师的判断，不是事实陈述）
      ]
    }}
  ],
  "follow_ups": ["下周值得跟进的信号1", "信号2", ...]
}}

**history_comparison 写法指引**（关键，请遵循）：
- **RAG 命中数为 0 的主题**：`history_comparison` 必须为空数组 `[]`，禁止写「暂无历史」类占位条目，禁止空 past_aid
- **条数**：按「各主题 RAG 历史文章命中数」决定；**禁止为凑满条数编造**
- 每条必须包含 **past_part + past_aid + this_week_part + aid** 四个字段
- past_aid 必须来自「RAG 历史文章清单」，aid 必须来自「本周文章摘要清单」
- 不要在 past_part / this_week_part 里写 week_id 或日期（代码会按文章 publish_date 自动显示）
- 每条聚焦不同对比维度（参与方、技术路线、频次等）

**insights 写法指引**：
- 基于 history_comparison 的演变轨迹，给出**为什么这很重要**的分析师判断
- 可包含：这个趋势的加速意味着什么、哪些玩家将受益/受损、读者应关注什么信号
- 不是事实堆砌，是分析与判断

覆盖 Phase B 全部主题；单篇低置信度话题可合并为「本周快讯」。
"""

PROFILE_PROMPT = """你是内容分析专家。根据以下公众号近 6 个月的文章摘要，推断其内容画像，并识别正文中的固定模板文字。

## 公众号
- nickname: {nickname}
- fakeid: {fakeid}

## 文章摘要样本（title + summary + topic_tags）
{summaries_compact}

## 正文头尾原始片段（2-3 篇，用于识别固定模板）
{raw_snippets}

## 推断规则

**insight_lens**（选一个，根据文体与话题分布，不依赖公众号名称）
- industry：主要内容是产业报道 / 投融资 / 商业化进展
- interview：主要内容是访谈 / 播客整理 / 人物观点
- science：主要内容是科普 / 学术研究 / 方法论
- business：主要内容是商业分析 / 消费 / 宏观经济
- general：内容混杂或无法归类（confidence < 0.6 时强制选此项）

**insight_tags**（3-6 个，取高频 topic 的上位概念，如「融资」「产业洞察」）

**confidence**（0.0-1.0）
- 0.8+：文体一致，话题集中
- 0.5-0.8：风格尚清晰但有杂项
- <0.6：风格混杂或样本不足，必须选 general

**evidence**（2-3 条推断依据，如「80% 文章含融资数据」）

**sample_aids**（2-3 个最能代表该号风格的 aid）

**strip_head_pattern**（正文开头固定模板，逐字匹配前缀）
- 若多篇文章正文**开头**有相同的套话/关注引导/免责声明等固定文字，填入该段完整文字
- 若开头无重复模板，填空字符串 ""

**strip_tail_markers**（结尾截断标记列表）
- 若多篇文章**结尾**有相同的分割线/关注引导/版权声明，填入触发截断的标志性字符串（如 "特 别 提 示"、"关注我们"）
- 代码遇到列表中任一字符串即截断其后所有内容
- 若结尾无重复模板，填空列表 []
"""

BOOTSTRAP_PROFILE_PROMPT = """你是内容分析专家。以下公众号尚无足够摘要，请根据**近期文章标题**和**正文头尾片段**推断其内容画像（置信度应偏保守）。

## 公众号
- nickname: {nickname}
- fakeid: {fakeid}

## 近期文章标题（最多 40 条）
{titles_compact}

## 正文头尾原始片段（2-3 篇，用于识别固定模板）
{raw_snippets}

## 推断规则
- insight_lens：industry / interview / science / business / general
- insight_tags：3-6 个上位概念
- confidence：标题样本通常应 ≤ 0.65；若无法判断选 general 且 confidence < 0.6
- evidence：2-3 条依据（引用标题中的高频词或话题）
- sample_aids：返回空列表

**strip_head_pattern**
- 若多篇正文**开头**有相同套话，填完整文字；否则填 ""

**strip_tail_markers**
- 若多篇正文**结尾**有相同分割线/关注引导，填标志性字符串列表；否则填 []
"""
