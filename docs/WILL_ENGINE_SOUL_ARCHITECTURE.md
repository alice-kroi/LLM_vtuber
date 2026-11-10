# 意志脑灵魂架构设计（人格 / 记忆 / 经验 / 知识）

> **目标**：给意志脑（`WillEngineBrain`，代码注释中亦称"思维脑"）建立完整的**人格 / 记忆 / 经验 / 知识**四件套，
> 使其从"关键词路由 + 状态阈值"升级为**有内在独白、有记忆、有偏好、会从结果里学**的角色内心。
> **形态定位**：类似二次元动漫人格助手的"内心侧"——不是任务队列，而是"我想要什么、为什么想要、上次这样做结果如何"。
> **版本**：v1.7 ｜ 日期：2026-10-22 ｜ 状态：四件套 + 意志面板 + distilled 反思 + bond/routine + 自主执行已实现
> **实现落地**：`LLM_v2/core/will_soul.py`（`WillSoul`）+ `will_decisions` / `will_outcomes` 两表；
> `profile.yaml` 新增 `will_engine:` 内心段；意志脑决策与结果已接入；`GET /api/will`、`POST /api/will/export`
> 与 WebUI「🫀 意志」面板可观测（身份 / 签名 / 决策 / 经验 / 回放 / 导出）。
> **不新建 `will_experience` 表**：编码类经验复用 `persona/learning` 的 `learn_procedures`，
> 其它动作经验由 `will_decisions + will_outcomes` 现场聚合（遵循 `AI_LEARNING_CAPABILITY.md` §7.3 的合并约定）。
> **选型结论**：本地自研，**不引入** Mem0 / Zep / Letta 等外部记忆框架；sqlite 为主、Markdown 为辅。
> **情感路线**：**保留 PAD**（不换叙事式情感）；按心理学选型补 **A. ALMA 三层对齐** + **B. 文献坐标替换硬编码 delta**（详见 §1.5、§3.4）。

---

## 1. 背景与现状盘点

### 1.1 三脑分工（刚性约束，本设计不得越界）

```
外部输入 → 能力域（执行） → 操作脑（动手） → 意志脑（想说什么） → 主播脑（只说）
                                              ↑ 本设计的作用点
```

- 意志脑**不调工具**、**不发声**、**不产出最终台词**；只产出「动作 + 目标 + 理由」，以及面向观众的解说总结。
- 发声统一由 `tts` 域承担；能力域零音频职责（`emit TTS_SPEAK`）。
- 意志脑可写 state（`runtime.apply_event`，既有行为，保留）。

### 1.2 现有可复用资产（已在 V2 运行）

| 资产 | 位置 | 内容 | 本设计中的角色 |
|---|---|---|---|
| 人格档案 | [profile.yaml](file:///e:/GitHub/LLM_vtuber/persona/profile.yaml) + [persona.py](file:///e:/GitHub/LLM_vtuber/persona/persona.py) | SillyTavern Card V2 兼容：PAD baseline / Big Five / values / interests / speaking_style / backstory / lorebook / avatar / constraints | **人格件**的底座，新增 `will_engine:` 段承载内心侧 |
| 状态 + 持久化 | [state.py](file:///e:/GitHub/LLM_vtuber/persona/state.py) + [persona_runtime.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/persona_runtime.py#L27-L153) | PAD 三维 + energy/focus/social_need/curiosity + `state_events` 审计 + `decay()` | **记忆件**的存储机制（同库同 `StateDB` 模式） |
| RAG | [rag_adapter.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/rag_adapter.py) | Milvus `chat_history`，三因子评分（30% 时间衰减 + 50% 相关性 + 20% 重要性） | **知识件**的外部见闻来源（复用，不重建） |
| Lorebook | [persona.py](file:///e:/GitHub/LLM_vtuber/persona/persona.py#L168-L176) `match_lorebook` | 关键词 → 注入条目（现全部是"关于小鲤自己"的设定） | **知识件**的自身设定切片 |
| 能力域清单 | [domain_assembly.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/domain_assembly.py) `active_tool_names()` | 运行期实际挂载/激活的域与工具 | **知识件**的"我现在能做什么" |
| 经验底座（**当前未被 LLM_v2 引用**） | [learning/](file:///e:/GitHub/LLM_vtuber/learning) | `RewardTracker`（奖励量化）、`KnowledgeCurator`（高奖励 → L4 经验 / avoid_pattern）、[reflection.py](file:///e:/GitHub/LLM_vtuber/learning/reflection.py)（Generative Agents 式反思）、`FeedbackInjector` | **经验件**的方法论来源（思路复用，**实现走 sqlite**，不引入 LangGraph `InMemoryStore`） |

### 1.3 意志脑当前缺口

| 件 | 现状 | 缺口 |
|---|---|---|
| 人格 | `persona.to_prompt_block()` 只服务对外人设 | 无"内心视角"：没有叙事认同、没有内在动机排序、没有自身红线 |
| **情绪** | PAD 三维已有，但象限名是自制的 7 值；事件 → PAD 的 delta 硬编码在 [streamer.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/streamer.py#L186-L189) 等处 | ALMA 的 **emotion 层完全缺失**（事件与 mood 之间没有"情绪"这一环）；无 mood 强度；数值无文献出处 |
| 记忆 | 无 | 决策过程不落盘；`_task_monitor` 仅内存，重启即失 |
| 经验 | 无 | 不知道"哪种状态下做哪种事结果好"；失败原因不累积 |
| 知识 | 无（主播脑才有 lorebook/RAG） | 不知道当前挂了哪些域、能不能做某事 → 容易"想刷视频但域没挂"；书籍知识检索也在主播脑 |

### 1.4 外部调研结论（2026，供选型留档）

| 方向 | 代表 | 结论 |
|---|---|---|
| 记忆层 | Mem0（Apache-2.0，向量 + KV）；Zep/Graphiti（时序知识图谱，双时间戳，LongMemEval 63.8% vs Mem0 49.0%） | **不采用**。Zep 必须旁挂 Neo4j/FalkorDB，两者均需额外常驻服务，与"本地单机 + 已有 Milvus/SQLite"冲突 |
| 人格 + 记忆一体 | Letta/MemGPT：core memory blocks（`persona` + `human`）常驻上下文、归档按需检索、块可被 agent 改写 | **只借鉴理念**：常驻锚点不检索 + 归档按需检索（对应本设计的"人格块 + 记忆回放"） |
| 人格表征 | **Emergi-PersonaOS**（2026）：三层人格 = 特质性情 / 特征适应 / 叙事认同，区分"持久信念"与"当前情境激活" | **核心借鉴**：持久部分写 YAML，激活部分由 state 决定 |
| 记忆演化 | A-MEM（Zettelkasten 自动链接 + 记忆演化）；MindMemOS（实体/属性/时间三维 + Skill 共进化） | 思路记录，实现偏研究型，不引入 |
| 学术底座 | Generative Agents（memory stream + `recency×0.5 + relevance×3 + importance×2` + Reflection）；MARS（艾宾浩斯遗忘曲线） | **直接采用**：三因子已在 RAG 使用；遗忘曲线用于经验衰减 |
| 工程实践 | OpenClaw / Hermes：记忆落成工作区 Markdown（`SOUL.md` / `MEMORY.md` / `USER.md`）作唯一事实来源，冻结快照注入 | **部分采用**：Markdown 仅作**派生导出视图**，真源仍是 sqlite + YAML |

> **硬约束依据**：NeurIPS 2025（Abdulhai 等）实测 LLM 约 100 轮对话后开始**人格漂移**
> （先强遵守 → 逐渐软化 → 回归基座模型行为）。故人格不能只在 prompt 里写一次，
> 必须"常驻锚点 + 每次决策完整重注入"。

### 1.5 情绪与人格的心理学选型调研（2026-10-19）

**问题**：`state.pad` 三维数值 + 事件里硬编码的 delta 是否合适？有没有更专业的替代？

**结论：PAD 不需要换，缺的是它前面的"产生层"。** 情感计算领域的事实标准是
「**认知评估（appraisal）+ PAD 维度**」双栈——评估层负责"情绪怎么产生"，PAD 负责"情绪怎么
量化与表达"。中文综述亦指出离散描述模型类型有限、维度描述模型能精确量化并覆盖无限情感，
二者互补而非替代。工业方案普遍如此；Gebhard 的 ALMA 与 Marsella & Gratch 的 EMA 是经典实现。

| 模型 | 定位 | 对本项目的价值 |
|---|---|---|
| **ALMA**（Gebhard 2005, A Layered Model of Affect） | 三层 affect：personality(长期) / mood(中期) / emotion(短期)，emotion 作为"冲动"注入 mood；mood 是 PAD 空间中的一个点，**八象限定义离散 mood**，强度由到原点的距离派生 | **最高**：现有 `profile.yaml`=personality ✔、`state.pad`=mood ✔，**emotion 层完全缺失**；8 个标准八象限名可直接替换自制 7 值 |
| **OCC**（Ortony/Clore/Collins 1988, 2022） | 22 种情绪，来自三类评估：目标相关事件 / 智能体行动 / 对象属性 | 事件 → 情绪的判定依据；FAtiMA Toolkit 为成熟实现参考 |
| **Scherer CPM** | 四类刺激评估检查：相关性 / 后果 / 应对能力 / 规范显著性 | 评估层 4 个维度的命名依据 |
| **EMA**（Marsella & Gratch） | 评估变量：desirability / likelihood / expectedness / causal attribution / controllability / changeability → 离散情绪 + mood | 评估变量取值口径参考 |
| **Russell & Mehrabian (1977)** | 原始 **22 情绪 PAD 值表** | 替换硬编码 delta 的**数值来源**（B 项） |
| **HEXACO**（Ashton & Lee） | 六因素，多 **Honesty-Humility**（诚实-谦逊），覆盖"不谄媚/不操纵"轴 | 未采纳（见下） |
| **Schwartz 基本价值观** | 十项跨文化验证的价值优先级 | 未采纳（见下） |
| **SDT**（Deci & Ryan 2000） | 三大基本心理需求：autonomy / competence / relatedness | 未采纳（见下） |

**已采纳（A + B）**

- **A. ALMA 三层对齐**：mood 象限规范化为 ALMA 八象限 + `neutral`，补 `mood_strength`，新增 emotion 层 —— 详见 §3.4
- **B. 文献坐标替换硬编码 delta**：离散情绪 → PAD 坐标以外部数据文件承载，带来源与可信度标记 —— 详见 §3.4.3

**本次未采纳（留档，后续可再议）**

- **HEXACO + Schwartz**：Big Five 是业界默认答案且已在 [profile.yaml](file:///e:/GitHub/LLM_vtuber/persona/profile.yaml#L20-L33) 使用，无硬伤；换成 HEXACO 与 Schwartz 十值的收益（主要是理论出处）不足以覆盖改造与既有 persona 结构的对齐成本。保留 Big Five + 现有 `values`。
- **SDT 三需求替换四条资源条**：`social_need / curiosity / focus` 语义已明确，且被 [will_engine._decide_rule](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/will_engine.py#L671-L731) 与 `state.should_force_rest()` 等消费；改为 autonomy/competence/relatedness 会牵动决策规则重写，收益偏"理论出处"而非能力提升。保留四条资源条。
- **叙事式情感**（事件流 + LLM 生成情绪标签 + 叙事原因）：能彻底摆脱数值、更贴近角色表达，但会牺牲可复现性并引入 LLM 情绪跳变风险。**决定保留 PAD 路线**。

---

## 2. 总体设计

### 2.1 四件套职责

```
┌──────────────────────── 意志脑 · WillSoul ────────────────────────┐
│                                                                    │
│  ① 人格  WillIdentity   我"是谁"——叙事认同 + 内在动机 + 自身红线    │
│         （YAML 真源，宽松解析，新增字段无需改代码）                  │
│                                                                    │
│  ② 记忆  WillMemory     我"做过什么"——决策日志 + 结果 + 启动回放    │
│         （sqlite：will_decisions / will_outcomes）                  │
│                                                                    │
│  ③ 经验  WillExperience 我"学到了什么"——(状态, 动作, 结果) 三元组   │
│         （sqlite：will_experience + 定期反思 + 遗忘曲线衰减）        │
│                                                                    │
│  ④ 知识  WillKnowledge  我"现在能做什么 / 我懂什么"                  │
│         （运行期动态生成：能力域清单 + interests + lorebook 切片）   │
│                                                                    │
└───────────────────────────┬────────────────────────────────────────┘
                            │ build_decision_context(state)
                            ▼
                   决策 prompt（8 段装配） → JSON 动作
                            │
                            ▼
              WILL_ENGINE_ACTION → 操作脑执行 → TASK_DONE/FAILED
                            │                        │
                            └──── 回写记忆 + 经验 ◄───┘
```

### 2.2 设计原则

1. **复用优先**：人格沿用 `profile.yaml`，存储沿用 `StateDB` 模式，见闻沿用 RAG，方法论沿用 `learning/`；不新建外部服务。
2. **宽松可扩展**：人格段用 loose dict 承载，未知字段原样保留并注入 prompt → 用户后续补人格信息**无需改代码**。
3. **失败不阻塞**：四件套全部"尽力而为"，任何一件不可用（sqlite 锁、域清单取不到）只降级、不中断决策。
4. **不越权**：只写 `will_*` 自己的表与会话内 state；不调工具、不发声、不改其他脑状态。
5. **启发而非决定**：经验只作为决策输入之一，不硬性决定动作——仍走既有 `rule → api` 兜底链路。
6. **情绪有来源**：mood 数值不再由硬编码 delta 决定，而是走「事件 → 评估检查 → 离散情绪 → PAD 冲动」（§3.4）；emotion 只作一次性冲动，不驻留。

---

## 3. ① 人格件 WillIdentity

### 3.1 载体

`persona/profile.yaml` 新增顶层段 `will_engine:`。**未知顶层键不影响现有加载**（`PersonaProfile.from_dict` 只读已知键），新增段由 `WillSoul` 独立读取。

```yaml
# ---------- 意志脑内在视角（内心侧，与对外人设分离）----------
will_engine:
  # 叙事认同：我是谁（持久，不随状态变）——对应 Emergi-PersonaOS 的 narrative identity
  narrative_identity: |
    我是小鲤的「内心」。对外说话的是主播脑，我只负责想什么、要什么、为什么。
    我不想被模板框死，但也不会忘记自己是谁。

  # 内在动机（0-1 权重），决定空闲时"更想干什么"——替代冷冰冰的规则阈值
  drives:
    - { name: "好奇", weight: 0.85, hint: "想知道新东西，想看没看过的话题" }
    - { name: "陪伴", weight: 0.70, hint: "想有人说话，想被回应" }
    - { name: "成就", weight: 0.60, hint: "想把一件事做完做好" }
    - { name: "休整", weight: 0.50, hint: "累了就想停一下，不硬撑" }

  # 自身红线（区别于主播脑的 constraints：这是"内心不肯做的事"）
  boundaries:
    - "不替主播脑说话：我只产出『要说什么』，不产出最终台词"
    - "不绕过用户授权做危险操作"
    - "不编造自己没做过的事——经验库里没有的，就说没有"

  # 内在独白语气（影响 reasoning 文本风格，不影响对外台词）
  tone_inner: "内心独白式、略带傲娇、句子短、偶尔自嘲"

  # 主推偏好：好奇时的优先兴趣（从 interests 里挑，可留空＝不限制）
  preferred_interests: ["编程", "深度学习"]
```

### 3.2 解析策略（关键：为"后续补人格信息"服务）

- `WillSoul` 以 **loose dict** 读入 `will_engine` 段：已知键（`narrative_identity` / `drives` / `boundaries` / `tone_inner` / `preferred_interests`）做校验，**未知键原样保留**。
- 未知键会被序列化进"人格块"——用户加 `bond` / `routine` / `catchphrases_inner` 等字段即刻生效，**不需要改 Python**。
- 段缺失或解析失败 → 回落到仅用 `persona` 通用信息，不报错。

### 3.3 二次元人格助手预留位（已实现）

| 预留 | 用途 | 落地方式 |
|---|---|---|
| `bond` | 好感度 / 关系状态（对常驻观众的熟悉度） | **已实现**：`will_bond` 表（familiarity / attachment / interactions / last_seen，70 天半衰期衰减） |
| `routine` | 作息（早/晚问候、固定复盘） | **已实现**：`profile.yaml` 的 `routine` + heartbeat 定时分支（±5 分钟窗口，同日一次） |
| `catchphrases_inner` | 内心口头禅 | 直接加进 `will_engine` 段即可生效（未知键原样保留） |

> 2026-10-22 补充实现：
> - `will_bond` 在每次 `USER_MESSAGE` / `GIFT_RECEIVED` 时加深；规则 1 会优先找 bond 最高的常驻观众；
> - `quiet_hours` 内不自主决策、不主动说话；
> - `autonomous_execute: true` 时，安全动作（`explore_interest` / `watch_video` /
>   `browse_bilibili` / `code_action` / `list_files`）会 emit `WILL_ENGINE_ACTION`
>   交给操作脑执行；冷却 15 分钟、同动作 1 小时内去重；
> - 作息 `greet` / `rest_check` / `diary` 通过 `PROGRESS_REPORT` 让主播脑发声，
>   意志脑本身不 emit TTS。

### 3.4 情绪层：ALMA 三层对齐 + OCC→PAD 映射（A + B）

#### 3.4.1 ALMA 三层与现有资产的映射

| ALMA 层 | 时间尺度 | 本项目载体 | 现状 |
|---|---|---|---|
| personality | 长期 | `persona/profile.yaml`（Big Five + `baseline_mood`） | ✔ 已有 |
| mood | 中期 | [state.py](file:///e:/GitHub/LLM_vtuber/persona/state.py#L24-L76) `PAD` + `persona/state.db` | ✔ 已有，需规范化象限与强度 |
| **emotion** | **短期** | **新增** `persona/emotion.py` | **缺失** |

机制：personality 为 mood 提供回归目标（现有 `state.decay(baseline_pad)` 已实现）；
emotion 作为一次性"冲动"注入 mood 后自身快速消退，不长期驻留。

#### 3.4.2 mood 规范化（改 `persona/state.py`）

**八象限（替换现有自制 7 值）**，依据 P / A / D 符号：

| 符号组合 | ALMA 名称 | 中文 |
|---|---|---|
| +P +A +D | `exuberant` | 兴高采烈 |
| +P +A −D | `dependent` | 依赖 |
| +P −A +D | `relaxed` | 放松 |
| +P −A −D | `docile` | 顺从 |
| −P +A +D | `hostile` | 敌对 |
| −P +A −D | `anxious` | 焦虑 |
| −P −A +D | `disdainful` | 轻蔑 |
| −P −A −D | `bored` | 无聊 |
| 近原点（\|P\|,\|A\|,\|D\| 均 < 0.15） | `neutral` | 平静 |

**mood strength**：`strength = |PAD| / √3`，归一化到 [0, 1]（ALMA 用"到原点的距离"派生强度）。

**兼容改造清单（必须一次改齐，否则表情会切错）**：

| 位置 | 改动 |
|---|---|
| [state.py](file:///e:/GitHub/LLM_vtuber/persona/state.py#L58-L73) `dominant_quadrant()` | **保留方法名**（避免破坏既有调用方），内部改返回上表 9 值；新增 `mood_strength()` |
| [state.py](file:///e:/GitHub/LLM_vtuber/persona/state.py#L214-L244) `to_prompt_block()` 的 `quadrant_cn` 表 | 换成 9 个中文名 |
| [profile.yaml](file:///e:/GitHub/LLM_vtuber/persona/profile.yaml#L117-L131) `avatar.emotion_to_expression` / `emotion_to_tone` | 键从 5 个扩成 9 个 |
| [live2d_listener.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/io/live2d_listener.py#L27-L35) `QUADRANT_TO_EXPRESSION` 兜底表 | 键从 7 个扩成 9 个 |
| `LLM_v2/io/static/app.js` | 仅展示字符串，无需改 |
| [will_engine.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/will_engine.py#L681-L682) `params.mood` | 值随象限名自动变化，无需改 |

> 改造前 `QUADRANT_TO_EXPRESSION` 兜底为 `neutral_01`，不会崩，只会短暂表情不准；
> 因此该清单可在一次提交内完成，不需要灰度。

#### 3.4.3 emotion 层（新增 `persona/emotion.py`）

**管道**：`事件 → 评估检查 → 离散情绪 → PAD 冲动 → 注入 mood`

**评估检查（Scherer CPM 四类，精简为本项目可判定形式）**：

| 检查 | 本项目取值 |
|---|---|
| 相关性 relevance | 是否与我有关 / 是否新颖 |
| 后果 implication | 目标一致（好/坏）/ 是否出乎意料 / 紧迫性 |
| 应对能力 coping potential | 我能否处理 / 是否可控 |
| 规范显著性 normative significance | 是否符合我的 `values` 与 `constraints` |

**离散情绪集合（OCC 子集，14 种，覆盖现网全部事件）**：
`joy` / `gratitude` / `satisfaction` / `relief` / `pride` / `hope` / `interest` /
`distress` / `disappointment` / `fear` / `anger` / `shame` / `boredom` / `sadness`

**PAD 冲动数值来源（B 项）**

- 权威原表：**Russell & Mehrabian (1977)** 的 22 情绪 PAD 值 —— 复述见 [arXiv:2104.10117](https://arxiv.org/html/2104.10117v1) 附录 **Table 4**；本项目已将其 PDF 存档于 `docs/2104.10117v1.pdf` 并**逐行提取核对完毕**。另有中文综述《维度语音情感识别研究综述》（软件学报 2020, 31(8): 2465-2491）表 2 可作交叉参考。
- **离线核对结果（2026-10-19，v3：原表已取得，主表定为 `russell_mehrabian_1977_table4`）**：完整 22 行已提取入档（`_verified_reference_rows.table4_russell_mehrabian_1977_original_22`）。**并查明此前"两支表冲突"的真正原因——不是数值冲突，而是下游论文把 R&M 的 `terrified` 改名成了 `Fear`。** 原表两条都含：

| R&M 原表词条 | P | A | D | 被下游如何处理 |
|---|---|---|---|---|
| `afraid` | −0.64 | 0.60 | −0.43 | CSDN 整合表 / csroc 表取其值并命名为 `Fear` |
| `terrified` | −0.62 | 0.82 | −0.43 | arXiv:2404.07229 与 2507.22326 取其值并命名为 `Fear` |

  两者都源自 R&M，只是词条不同。本项目取 `afraid`（语义更贴"恐惧"，且被两个第三方来源复现），`terrified` 记入参考区备用。

  **顺带纠正 v2 的一处误判**：CSDN 那张整合表**并非 R&M 原表的忠实复述**——只有 Joy / Anger / Fear / Disgust 四条与 R&M 一致；它的 `Sadness`（−0.40 / −0.20 / −0.50）实为 csroc 表的 `pain`，与 R&M 的 `sad`（−0.64 / −0.27 / −0.33）不同。所以 v2 把 `sadness` 记成"3 源"、把 `disappointment` 标成 `verified_secondary` 都是错的，v3 已更正。

  **仍在登记的坑**：
  1. **原表疑似印刷错误**——Table 4 的 `devastated` 印为 (0.14, 0.45, −0.24)，与其自身 Table 5 的预测值 (−0.41, 0.10, −0.40) 及常识都不符。本项目 14 情绪未用到该词条，仅在参考区标 `anomaly`。
  2. **原表压根没有这 5 个词条**——`relief` / `interest` / `boredom` / `disappointment` / `distress` 均不在 R&M 22 条内，所以它们**不可能**是实证值。
  3. **命名映射是显式的**，非自动改名：`joyful→joy`、`grateful→gratitude`、`content→satisfaction`、`proud→pride`、`hopeful→hope`、`ashamed→shame`、`sad→sadness`、`angry→anger`、`afraid→fear`（完整映射见文件内 `_name_mapping`）。

- **落地方式：数据与代码分离**。[emotion_pad.json](file:///e:/GitHub/LLM_vtuber/persona/emotion_pad.json) 已升到 **v3**：`primary_lineage = "russell_mehrabian_1977_table4"`、`allow_derived = false`，附 `_lineage_resolution`、`_cross_checks`、`_name_mapping`、`_coverage`、`_fallback_policy`、`_known_issues`、`_verified_reference_rows`（含 Table 4 全 22 行 + Table 5 全 32 行）。14 个项目情绪状态：

| 状态 | 数量 | 情绪 |
|---|---|---|
| `verified_primary` | **9** | `joy`、`gratitude`、`satisfaction`、`pride`、`hope`、`fear`、`anger`、`shame`、`sadness` |
| `derived` | 4 | `relief`、`interest`、`distress`、`boredom` |
| `predicted_paper` | 1 | `disappointment`（取自该论文 Table 5 的正式预测条目 `disappointed`） |
| `verified_secondary` / `conflict` / `todo` | 0 | — |

  - **9 个实证条目可立即启用（覆盖 64%），已脚本校验：9 条数值与 PDF 中 Table 4 逐位一致（ALL CHECKS PASS）。**
  - **余 5 个需 `allow_derived=true`**。该开关现统管 `derived` 与 `predicted_paper` 两类非实证条目；规则均以 R&M 实证条目为锚点（`joyful` / `sad` / `impressed` / `surprised`），按 OCC 归因分类与 ALMA 八象限推导，逐条写明 `basis` 与 `rule`。
  - 代码规则不变：仅取 `verified_*`；非实证类看开关；`conflict` / `todo` 跳过并告警。

  **"补原文"这类待办已清空**——原表已到手并核完。剩下的唯一选择是那 5 个非实证条目开不开开关。

- **emotion 生命周期**：
  1. 事件到达 → 评估检查（规则判定，或由意志脑同一次 LLM 调用顺带产出）
  2. 判定离散情绪 `e`
  3. 查 `emotion_pad.json` 取 `(P, A, D)`，乘强度系数 `k`（低/中/高 → 0.3 / 0.6 / 1.0）
  4. `runtime.apply_event(f"emotion_{e}", delta_P=…, delta_A=…, delta_D=…)` → 注入 mood（走既有审计表）
  5. emotion 本身不驻留，只作为一次性冲动

- **与现有硬编码规则的关系**：现网硬编码 delta 分布在
  [streamer.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/streamer.py#L186-L189)（chat_received / chat_replied）、
  [streamer.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/streamer.py#L276-L278)（gift_received）、
  [streamer.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/streamer.py#L295-L301)（task_done / task_failed）、
  [will_engine.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/will_engine.py#L665-L668)（will_engine_decided）。
  改造采用**灰度替换**：命中 emotion 管道的事件走新路径，未覆盖的事件继续走旧硬编码，
  两者不会同时作用同一事件。

---

## 4. ② 记忆件 WillMemory

### 4.1 表结构（沿用 `persona/state.db` 同一 sqlite 库）

```sql
-- 决策日志：我做过什么决定
CREATE TABLE IF NOT EXISTS will_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at    TEXT NOT NULL,     -- ISO 时间
    trigger       TEXT,              -- idle / user_message / task_finished / manual
    mode          TEXT,              -- rule / api / hybrid
    state_snapshot TEXT,             -- JSON: {quadrant, P, A, D, E, F, S, C}
    action_type   TEXT,              -- social_action / explore_interest / watch_video / ...
    action_target TEXT,
    reasoning     TEXT,              -- 一句话理由（内在独白）
    confidence    REAL,              -- 0-1，规则决策给高值，API 决策取解析置信
    task_id       TEXT,              -- 关联后续执行（可空）
    created_at    TEXT
);

-- 决策结果：那次决定后来怎么样了
CREATE TABLE IF NOT EXISTS will_outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id  INTEGER NOT NULL,   -- 关联 will_decisions.id
    task_id      TEXT,
    finished_at  TEXT,
    success      INTEGER,            -- 1 / 0
    duration_sec REAL,
    error        TEXT,
    reward_delta REAL DEFAULT 0,     -- 观众侧反馈（礼物/好评/差评，可空）
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_will_dec_at   ON will_decisions(decided_at);
CREATE INDEX IF NOT EXISTS idx_will_out_dec  ON will_outcomes(decision_id);
```

### 4.2 读写时机（事件钩子映射）

| 时机 | 事件 / 位置 | 动作 |
|---|---|---|
| 决策产生 | `heartbeat` 决策后（`emit WILL_ENGINE_DECIDED` 之前） | `record_decision()` → 返回 `decision_id` |
| 下发执行 | `emit WILL_ENGINE_ACTION` | payload 带 `decision_id`，由操作脑回带 |
| 任务结束 | `_on_task_finished`（`TASK_DONE` / `TASK_FAILED`） | `record_outcome(decision_id, success, duration, error)` |
| 进程启动 | `WillSoul` 首次 `build_decision_context()` | **启动回放**：取最近 20 条决策 + 结果压成文本 |

### 4.3 启动回放（解决"重启失忆"）

- 取 `will_decisions` 最近 20 条，LEFT JOIN `will_outcomes`；
- 压成一段紧凑文本（每行 ≤ 90 字），形如：

```
昨天 21:03 状态 relaxed|E-mid|S-low → watch_video（心情好想刷视频）→ 成功 12s
昨天 21:40 状态 docile|E-low|S-mid  → rest（有点累）          → 成功 30s
```

- 注入"记忆块"，token 上限 400（超出则只保留最近 10 条）。

### 4.4 Markdown 辅助视图（派生导出，只读）

- `persona/will_soul_report.md`：由 `WillSoul.export_markdown()` 生成，含
  - 人格块全文（YAML 段快照）
  - 最近 30 条决策 + 结果
  - 经验库 Top-N（按 `weight × avg_outcome`）
- 用途：人工查阅与校准。**不是真源**，不参与写回；真源是 YAML（人格）+ sqlite（记忆/经验）。

---

## 5. ③ 经验件 WillExperience

### 5.1 状态签名（离散化，保证可聚合）

```
signature = "{mood}|E-{lv}|S-{lv}|C-{lv}"

mood : state.pad.dominant_quadrant() 的 9 值（ALMA 八象限 + neutral，见 §3.4.2）
       exuberant / dependent / relaxed / docile / hostile / anxious /
       disdainful / bored / neutral
E 档 : E-low(<30) / E-mid(30-69) / E-high(>=70)
S 档 : S-low(<40) / S-mid(40-69) / S-high(>=70)
C 档 : C-low(<40) / C-high(>=40)

组合上限 = 9 × 3 × 3 × 2 = 162，可控不爆炸
```

### 5.2 表结构

```sql
CREATE TABLE IF NOT EXISTS will_experience (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    state_signature TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    attempts        INTEGER DEFAULT 0,
    successes       INTEGER DEFAULT 0,
    avg_outcome     REAL    DEFAULT 0,   -- 归一化 [-1, 1]
    weight          REAL    DEFAULT 1.0, -- 遗忘曲线衰减后的当前权重
    last_seen       TEXT,
    distilled       TEXT,                -- 定期反思归纳出的一句话（可空）
    updated_at      TEXT,
    UNIQUE(state_signature, action_type)
);
```

### 5.3 结果分归一化

```
success_sign = +1（TASK_DONE/成功） / -1（TASK_FAILED/失败）
reward_norm  = clamp(reward_delta / 10, -1, 1)      # 观众侧反馈（可空 → 0）
outcome      = 0.7 * success_sign + 0.3 * reward_norm
```

`avg_outcome` 增量更新（不需要历史全量）：

```
avg_outcome ← (avg_outcome * attempts + outcome) / (attempts + 1)
successes   ← successes + (1 if success else 0)
attempts    ← attempts + 1
```

### 5.4 遗忘曲线衰减（借鉴 MARS / 艾宾浩斯）

```
weight = exp(-λ * days_since(last_seen))，λ = 0.02   # 半衰期 ≈ 35 天
```

- 每次 `upsert` 时先按上式衰减旧 `weight`，再 `weight = min(1.0, weight + 0.15)`（被再次验证则回血）。
- 检索时用 `weight × avg_outcome` 作为排序键；`weight < 0.05` 视为过期，不注入。

### 5.5 检索与注入

- 以当前 `state_signature` 取 **同签名** 经验（`k=3`），不足则取 **同象限** 经验补齐；
- 压成"经验块"，形如：

```
- 在这种状态下做 watch_video：试过 4 次，成功 3 次，平均结果 +0.62
  → 上次你这样刷视频，观众反应不错，可以再来
```

- token 上限 200，最多 3 条。

### 5.6 反思归纳（借鉴 [reflection.py](file:///e:/GitHub/LLM_vtuber/learning/reflection.py)，异步、节流）

- 触发：每 60 分钟 或 每累积 20 条新结果（取先到者），且距上次反思 ≥ 30 分钟；
- 输入：`attempts >= 3` 的高/低分经验记录；
- 用 LLM 产出 2-3 条一句话经验（"在这种状态下，做 X 通常有好处 / 通常没用"），写回 `distilled` 字段；
- **低置信度（< 0.4）不写回**，防错误认知污染；
- 异步执行，不阻塞决策；LLM 不可用时静默跳过。

---

## 6. ④ 知识件 WillKnowledge

全部**运行期动态生成**，无独立存储（可视为"每次现算的只读视图"）。

### 6.1 四个来源

| 来源 | 取法 | 产出文本 |
|---|---|---|
| 能力清单（最重要） | `runtime.assembly.active_tool_names()` + 域 label | "我现在能做：B 站视频、启动器、桌面操作、TTS、Live2D …" |
| 自身兴趣 | `persona.interests`，按 `enthusiasm` 取前 5，并优先 `will_engine.preferred_interests` | "我偏好：编程、深度学习、动漫 …" |
| 自身设定 | `runtime.persona.match_lorebook(text, top_k=2)` | 现有 lorebook 全部是"关于小鲤自己"的设定，天然符合 |
| **知识库资料** | `LLM_v2/core/will_knowledge.py`：`knowledge_chunks` 召回 20 块 → rerank（本地 cross-encoder 或 LLM）挑 10 出 3 → 发布共享缓存 → 主播脑只读 | "【知识库资料】1. [书名 p.页码] …"；检索太弱时记知识缺口 |

> **归属修正（v1.8）**：书籍/资料检索归思维脑。主播脑不再直接调用知识库，
> 只在回复时读取思维脑发布的缓存（`WillKnowledge.read`，可短暂等待）。
> 缺口会写入 `【我还没弄明白的】`，空闲决策可把该话题作为 `explore_interest`
> 的目标，走既有的联网研究（Operator `research_task`）→ 笔记 → 下次可检索。


### 6.2 为什么必须有"能力清单"

意志脑当前会在无域支撑时仍然"想"做某事（例：想刷 B 站但 `bili_video` 未激活）。
把运行期真实域清单注入决策 prompt，可显著减少这类**能力幻觉**；若 `assembly` 取不到 → 该块留空并记 warning，不阻塞。

---

## 7. 决策 prompt 装配（8 段）

| # | 段 | 来源 | token 上限 | 缺失时 |
|---|---|---|---|---|
| 1 | 大脑身份 | `runtime.build_system_prompt("will_engine")` 的 brain_intro | 120 | 必有 |
| 2 | **人格块** | `will_engine` 段（叙事认同 + 动机 + 红线 + 语气 + 未知扩展键） | 300 | 回落 `persona.to_prompt_block()` 前半 |
| 3 | 状态块 | `state.to_prompt_block()`（含 ALMA 象限名 + mood strength） | 150 | 必有 |
| 4 | **记忆块** | 最近 20 条决策 + 结果（启动回放） | 400 | 空则省略整段 |
| 5 | **经验块** | 当前状态签名 Top-3 经验 | 200 | 空则省略整段 |
| 6 | **知识块** | 能力清单 + 兴趣 + lorebook 触发 | 250 | 各子项独立降级 |
| 7 | 任务提示 | "你现在空闲了一会儿，根据以上决定想做什么" | 40 | 必有 |
| 8 | 输出格式 | 严格 JSON：`{"type","target","reasoning"}` | 60 | 必有 |

**附加总量目标 ≤ 1300 tokens**；超出则按 `记忆 → 经验 → 知识` 顺序截断（人格与状态永不舍弃）。

装配顺序即优先级：**人格（我是谁）> 状态（我现在怎样）> 记忆（我做过什么）> 经验（我学到什么）> 知识（我能做什么）**。

---

## 8. 与既有架构的边界

| 约束 | 本设计的遵守方式 |
|---|---|
| 意志脑不调工具 | 只输出动作建议，执行由操作脑 + 能力域完成 |
| 意志脑不发声 | 不 emit `TTS_SPEAK`；解说走既有 `PROGRESS_REPORT → 主播脑` |
| 不替代主播脑总结 | `_narrate_bili_video_content` 维持现状（意志脑总结 → 主播脑只说） |
| 不硬编码凭据 | 无凭据需求 |
| 不破坏 `--no-*` 参数语义 | 四件套仅读写 sqlite/YAML，不参与域启停判定 |
| 失败隔离 | 每个方法独立 try/except + 降级；异常不得逃逸到 `heartbeat` |

---

## 9. 文件清单与改造点

| 类型 | 文件 | 内容 |
|---|---|---|
| 新增 | `LLM_v2/core/will_soul.py` | `WillSoul` 类：身份解析 / 决策记录 / 结果记录 / 经验 upsert + 检索 / 状态签名 / 启动回放 / Markdown 导出 |
| 修改 | `persona/profile.yaml` | 新增 `will_engine:` 段（§3.1） |
| 修改 | `LLM_v2/core/persona_runtime.py` | 新增 `runtime.will_soul` 懒加载属性（与既有 `assembly` 属性同构） |
| 修改 | `LLM_v2/brains/will_engine.py` | `_decide_api` 改用 `build_decision_context()`；`heartbeat` 落决策；`_on_task_finished` 落结果 |
| 新增 | `persona/emotion.py` | 情绪层：评估检查 → 离散情绪 → PAD 冲动（A + B，§3.4.3） |
| 新增 | `persona/emotion_pad.json` | 离散情绪 → PAD 坐标表（数据文件，带 `_source` 与逐条 `status`） |
| 修改 | `persona/state.py` | `dominant_quadrant()` 改返回 ALMA 9 值；新增 `mood_strength()`；`quadrant_cn` 中文名表更新（§3.4.2） |
| 修改 | `persona/profile.yaml` `avatar.*` | `emotion_to_expression` / `emotion_to_tone` 键扩成 9 值 |
| 修改 | `LLM_v2/io/live2d_listener.py` | `QUADRANT_TO_EXPRESSION` 兜底表键扩成 9 值 |
| 修改 | `LLM_v2/brains/streamer.py` | 5 处硬编码 `apply_event` delta 灰度改走 emotion 管道 |
| 修改 | `LLM_v2/core/persona_runtime.py` `StateDB` | 追加三张表的 `CREATE TABLE IF NOT EXISTS`（幂等，不迁移旧数据） |
| 新增（可选，P3） | `LLM_v2/io/webui.py` + `static/` | "意志脑"面板：决策列表 / 经验库 / 导出按钮 |
| 新增（测试） | `scripts/test_will_soul.py` | 见 §11 |

> **不改**：`learning/`（仅借用方法论）、`memory_manager.py`（保持 V1 遗留态，不被 V2 引用）。

---

## 10. 分期实施

| 阶段 | 内容 | 交付 |
|---|---|---|
| **P0** 地基 | `WillSoul` 骨架 + 人格段（§3）+ 决策/结果两表（§4）+ 启动回放 + 决策 prompt 接入（§7 段 1-4、7-8） | 意志脑能"记得自己做过什么" |
| **P0.5** 情绪层 | ALMA 三层对齐（八象限 + `mood_strength`，§3.4.2 兼容清单一次改齐）+ `emotion_pad.json` + `emotion.py` 管道（§3.4.3，灰度替换） | 情绪从"硬编码 delta"变成"评估 → 情绪 → 冲动" |
| **P1** 经验 | 状态签名 + 决策结果现场聚合（不建 `will_experience`）+ 衰减 + 经验注入/规则修正（§5.1-5.5、§7 段 5） | 意志脑能"根据以往结果调整倾向" |
| **P2** 知识 + 反思 | 能力清单动态生成（§6）+ 定期反思归纳 `distilled`（§5.6）+ Markdown 导出（§4.4） | 四件套完整闭环 |
| **P3** 可视化 | WebUI 意志脑面板 + 二次元预留位（§3.3） | 可观测、可人工校准 |

> 2026-10-22 实测：WebUI 输入「截屏」→ WillEngine 路由 `os_capture_window` → `will_decisions` 落库
> （decision_id 经操作脑透传到 `TASK_DONE`）→ `will_outcomes` 回填 success=1 → 经验聚合
> `execute_tool 1/1 +0.70`；「🫀 意志」面板与 `will_soul_report.md` 导出均验证通过。
> 自主心跳（`--autonomous`）实测：决策 `explore_interest` → 自动执行
> `os_browser_search` → `will_outcomes` 回填 success=1；「🫀 意志」面板同步显示
> 成功决策与 `web_console` 的 bond；安静时段与 15 分钟执行冷却生效。

每阶段独立可回滚：P0 完成后即使 P1/P2 未做，系统行为与现状兼容（新块缺失时整段省略）。

---

## 11. 验证方案

### 11.1 单元/脚本（`scripts/test_will_soul.py`）

| 项 | 断言 |
|---|---|
| 人格段解析 | 已知字段解析正确；**未知字段被保留**并出现在人格块中；段缺失时不抛异常 |
| 决策落库 | `record_decision()` 返回有效 `id`；`state_snapshot` 可 JSON 反序列化 |
| 结果关联 | `record_outcome()` 后 `decisions LEFT JOIN outcomes` 能取到 success |
| 状态签名 | 对同一 state 稳定；对跨档位的 state 产生不同签名；组合不超出 126 |
| 经验 upsert | 同 `(signature, action_type)` 幂等累加；`attempts`/`successes` 单调不减 |
| 衰减 | `weight` 随 `last_seen` 变旧而单调下降；再次 upsert 后上升且 ≤ 1.0 |
| 启动回放 | 有历史时非空且 ≤ 400 token 量级；无历史时为空串（不产生空块标题） |
| prompt 装配 | 8 段顺序正确；任一段缺失不留空标题；附加总量 ≤ 1300 token 估算值 |
| 降级 | 故意损坏 sqlite / 清空 assembly → 决策仍能产出动作，不抛异常 |
| 八象限映射 | P/A/D 符号组合 → 9 值映射正确；近原点 → `neutral` |
| mood strength | `\|PAD\|` 增大时 strength 单调不减；范围恒在 [0, 1] |
| 坐标表守门 | `status != verified_*` 的条目被跳过且产生 warning；可疑数值绝不出现在任何 delta 中 |
| 情绪管道互斥 | 同一事件不会同时命中 emotion 管道与旧硬编码（灰度期间"命中即互斥"） |

### 11.2 端到端

1. 启动：`python -m LLM_v2.main --autonomous --no-bilibili`（TTS / Live2D 按需）
2. 观察日志：`will_decisions` 落库、`WILL_ENGINE_DECIDED` 的 reasoning 是否引用到记忆/经验
3. WebUI 发一条指令 → 触发 `WILL_ENGINE_ACTION` → 任务结束 → 检查 `will_outcomes` 与 `will_experience` 是否被写
4. 重启进程 → 确认启动回放中出现上一轮决策（验证"跨重启不丢"）

> 依用户习惯：测试可指定 `--no-live2d` / `--no-tts` 隔离变量；P0 阶段可用 `--no-bilibili` 减少噪声。

---

## 12. 风险与边界

| 风险 | 说明 | 缓解 |
|---|---|---|
| 状态签名过粗 | 162 组合仍可能把不同情境混为一谈 | 先跑通，后续按需加维度（focus 档 / 时段） |
| 经验冷启动 | 早期样本少，"经验块"可能误导 | `attempts < 3` 不注入；低置信不写回 |
| prompt 膨胀 | 8 段叠加挤压模型注意力 | 硬 token 预算 + 截断顺序明确（§7） |
| 人格漂移 | 长会话后内心人格软化 | 每次决策完整重注入人格块（不做增量）；Markdown 导出供人工校准 |
| 与主播脑人格分裂 | 内心语气 ≠ 对外语气，可能违和 | `tone_inner` 只作用于 `reasoning`（内部），不进入对外台词 |
| sqlite 并发 | 多线程写（heartbeat / event handler） | 复用 `StateDB` 的 `threading.Lock` 模式；写失败仅告警 |
| 坐标表来源不一致 | 同一情绪（如 Fear）在不同二手文献中 PAD 数值差异大，甚至 A/D 符号相反 | `emotion_pad.json` 每条带 `_source` + `status`；非 `verified_*` 跳过并告警；**锁定单一权威原表，禁止混用** |
| 八象限改造波及表情 | `dominant_quadrant()` 返回值变化会同时影响 Live2D 兜底表、profile avatar 映射、WebUI 展示 | §3.4.2 兼容清单一次改齐；兜底为 `neutral_01` 不会崩，仅短暂表情不准 |
| emotion 与旧 delta 并存期 | 灰度期间两套机制共存，同类事件口径可能不一致 | 明确"命中即互斥"；经验签名保留事件类别以便区分来源 |

---

## 13. 决策记录与待确认

### 13.1 已确认（2026-10-19）

| 决策 | 结论 |
|---|---|
| 技术路线 | **本地自研四件套**，不引入 Mem0 / Zep / Letta |
| 持久化载体 | **sqlite 为主、Markdown 为辅**（Markdown 仅派生导出，不是真源） |
| 交付顺序 | **先出设计文档，再编码** |
| 情感路线 | **保留 PAD**，不采用"事件流 + LLM 生成情绪标签"的叙事式替代 |
| 心理学升级 | **采纳 A（ALMA 三层对齐）+ B（文献坐标替换硬编码 delta）** |
| 未采纳 | C（HEXACO + Schwartz）／ D（SDT 三需求替换资源条）—— 理由见 §1.5 |
| 落地节奏 | P0 地基 → P0.5 情绪层 → P1 经验 → P2 知识+反思 → P3 可视化（§10） |
| 坐标表主表 | **最终采用 R&M 1977 原表 Table 4**（`primary_lineage = "russell_mehrabian_1977_table4"`）。原表已从 `docs/2104.10117v1.pdf` 逐行取得并核验；v2 曾以 CSDN 整合表作主表，v3 因查明其非忠实复述而更正 |

### 13.2 仍待确认

1. §7 的 8 段装配顺序与 token 预算（附加 ≤ 1300）是否认可？
2. `will_decisions` 是否要记录"被否决 / 被抢占"这类非执行结果？（当前只记 `TASK_DONE` / `TASK_FAILED`）
3. Markdown 导出路径用 `persona/will_soul_report.md` 是否合适？
4. `emotion_pad.json` 的 5 个非实证条目（`derived`：`relief` / `interest` / `distress` / `boredom`；`predicted_paper`：`disappointment`）是否开 `allow_derived=true`？不开就只有 9 个实证条目可用（已是主体）；开则补满 14 个。→ **待你在 P0.5 开工前定**。
5. ALMA 八象限的中文名（兴高采烈 / 依赖 / 放松 / 顺从 / 敌对 / 焦虑 / 轻蔑 / 无聊）是否要按小鲤的人设语气重写（如"得意 / 黏人 / 蔫了"）？注意：对外台词不受影响，此处只作用于内部状态描述与表情键。

---

**版本**：v1.3 ｜ 日期：2026-10-19 ｜ 状态：设计待确认（含情绪层 A+B 升级；坐标表已取得 R&M 原表并核验）
