# AI 学习能力设计（v1.0）

> 状态：P0 偏好 / P1 情景召回 / P1b 程序性记忆 / P2 规则巩固+经验反思 / P3 技能演化已实现
> 目标：让小鲤从**互动、任务成败、自己造的能力**中持续积累，且人格不被学习改坏
> 前置：`docs/WILL_ENGINE_SOUL_ARCHITECTURE.md`（意志脑四件套）、`docs/AI_SELF_CAPABILITY_ACQUISITION.md`（能力自造与准入）

---

## 0. 摘要

"学习能力"不是一个模块，而是四条独立链路，共用一套记录与反思机制：

| 类型 | 一句话 | 落地位置 |
|---|---|---|
| 情景记忆 | 发生过什么 | `learn_episodes` |
| 偏好记忆 | 这个人喜欢什么 | `learn_preferences` |
| 程序性记忆 | 这类任务该怎么做 | `learn_procedures` |
| 技能演化 | 我造的能力哪个好用 | `persona/capabilities.json` 增强 |

核心设计取舍：

1. **不训模型、不改权重**。全部学习落在外部记忆与规则上，人格可复现、可回滚。
2. **不用额外奖励模型**。`Self.perceive()` 产出的 `emotion + intensity` 已经是带解释的即时奖励，直接复用。
3. **人格段永远只读**。学习只能改经验、流程、偏好，不能碰 `profile.yaml` 的核心字段。
4. **每条经验必须可审计**。能回答"从哪几次经历学到""成功过几次""什么时候该淘汰"。

---

## 1. 目标与非目标

### 1.1 目标

- 同一个用户第二次聊天时，小鲤记得他的偏好和上次聊过什么
- 同类编码任务第二次执行时，能复用上次成功路径，减少侦察轮次
- 自己造的能力用久了能自动分出"好用 / 该淘汰"
- 空闲时自动整理记忆，而不是无限追加

### 1.2 非目标（明确不做）

- **不做 LoRA / 微调**。MetaClaw 那类"用户不活跃时更新权重"的方案需要云端 GPU，且会让同一输入产生不同输出，破坏人格可复现性。
- **不做向量库扩容**。Milvus 已有，只用于知识检索；学习结果走结构化 SQLite，便于精确审计。
- **不做跨用户人格漂移**。学到的偏好按 `user_id` 隔离，不汇总成"整体人格"。
- **不做无证据的自我修改**。任何被固化的流程必须有 ≥2 条成功轨迹支撑。

---

## 2. 现状盘点

### 2.1 可以直接复用的资产

| 资产 | 位置 | 现状 | 在学习中的角色 |
|---|---|---|---|
| 情绪与关系 | `persona/self.py` | 已实现（ALMA 三层 + 衰减 + 关系） | **即时奖励信号源** |
| 情绪评估 | `persona/emotion.py` | 已实现（Scherer 四类 → OCC → 强度） | 结果解释，附带"为什么好/坏" |
| 关系表 | `persona/relationships.json` | 已实现（trust/intimacy/affinity） | 长期价值权重 |
| 编码轨迹 | `state.db: coding_sessions / coding_steps` | 已实现（轮次、工具、成败） | **程序性记忆的原料** |
| 能力登记 | `persona/capabilities.json` | 已实现（风险、闸门、成功率、熔断） | 技能演化的状态机 |
| 能力闸门 | `LLM_v2/domains/capability_ops/` | 已实现（G1/G2/G3 + 熔断回滚） | 新技能准入 |
| 状态审计 | `state.db: state_events` | 已实现 | 事件时间线 |
| 旧学习底座 | `learning/*.py` | **已存在但 LLM_v2 完全未引用** | 参考实现，不直接接入 |

### 2.2 缺口

- 没有"事件内容"表：`state_events` 只记 delta，不记"谁说了什么"
- 没有偏好表：关系只有两个数值，没有"喜欢猫、讨厌被叫老板"
- 没有流程表：`coding_steps` 是一次性的，没被压缩成可复用步骤
- 没有反思任务：所有学习都得在对话轮次里做，没有空闲/夜间批处理
- `will_experience` 只在设计文档里，表未建

---

## 3. 四类学习模型

### 3.1 情景记忆（episodic）

**记什么**：一次互动的最小事实集——谁、什么时候、说了什么、小鲤怎么回、结果如何。

**不记什么**：完整聊天记录（那是 `memory_manager` 的活）；这里只记"值得回忆的片段"。

**触发**：满足任一即记录
- 产生了 `intensity ≥ 0.5` 的情绪
- 关系变化超过阈值（`Δaffinity ≥ 0.02`）
- 任务成功或失败
- 用户显式说"记住"

**保留策略**：按重要度（`|emotion.intensity| + 关系变化`）排序，低于阈值的 30 天后淘汰。

**召回（P1 已实现）**：`persona/learning/episode.py`

```
score = importance × exp(-0.02 × days) + 关键词加成（2-gram 命中，最多 +0.45）
```

- 只召回当前 `user_id`，最多 3 条注入 `【我记得的事】`
- 低于 `0.30` 不注入；相同摘要去重
- 90 天前的普通条目（importance 1.0）分数 0.165，自然淡出

### 3.2 偏好记忆（preference）

**记什么**：`user_id + 维度 + 取值 + 置信度 + 证据`。

维度是开放集合，初期只做四个：
- `称呼`：希望被怎么叫
- `话题`：喜欢/讨厌聊什么
- `风格`：喜欢简短/详细、正经/玩梗
- `雷点`：明确反感的东西

**怎么产生**：
1. 规则抽取（零成本）：出现"我喜欢/我最喜欢/别叫我/不要提"等句式时，直接抽
2. 反思抽取（低成本）：会话结束时把最近 N 轮交给模型总结，产出候选偏好
3. 置信度累加：同一条被独立观察到 2 次以上才生效（单次可能是玩笑）

**怎么用**：`Self.to_prompt_block()` 里加一节"对这个人的了解"，只注入当前对话对象的偏好，最多 5 条。

### 3.3 程序性记忆（procedure）

**记什么**：把成功轨迹压成三段式技能（借鉴 Skill-Pro 的 activation / execution / termination）：

```json
{
  "signature": "编码|新建能力域|E2|S1|C2",
  "when": "需要新增一个 capability 域，且工作区为项目根目录",
  "steps": [
    "code_git_status 检查工作区干净",
    "读取一个现成域作为模板（hf_hub）",
    "创建 __init__.py / <id>.py / <id>_tools.py / manifest.json",
    "os_run_command 跑一次 import 冒烟"
  ],
  "done_when": "G2 子进程导入成功且 G3 冒烟通过",
  "avoid": ["连续 3 轮只读侦察（浪费轮次）", "工厂返回 dict 而非域实例"]
}
```

`avoid` 字段来自失败轨迹，比正面步骤更有价值——这正是 ExpeL 的核心发现：**失败对比产生的规则迁移性更强**。

**signature 的 E/S/C 分档**：取自 `Self` 的资源条，三档（0 低 / 1 中 / 2 高），阈值 30 / 65。

| 档位 | 含义 | 来源字段 | 对流程的影响 |
|---|---|---|---|
| E | 精力 | `energy` | 高精力更适合长流程；低精力优先短路径 |
| S | 社交需求 | `social_need` | 高社交需求容易被打断，流程更短 |
| C | 好奇 | `curiosity` | 高好奇倾向探索，低好奇倾向复用旧路径 |

签名形如 `编码|新建能力域|E2|S1|C2`；召回时先按任务类型前缀取候选，再按
档位距离（E/C 权重 1.0、S 权重 0.5）选最近的一条。

**怎么产生**：任务成功且 `coding_sessions.status=done` 后，异步抽一次，产出候选流程。

**怎么用**：编码循环启动时，按 `signature` 召回一条流程，注入 PLAN 阶段，减少侦察轮次。

### 3.4 技能演化（capability evolution）

在现有 `capability_ops` 上补三步，参考 OpenSpace 的 `FIX / DERIVED / CAPTURED`：

| 状态 | 含义 | 现在 | 要补 |
|---|---|---|---|
| `derived` | 单次成功生成 | 有（自动准入） | — |
| `captured` | 多次运行后融合成稳定版 | 无 | 同类能力出现 2 次时合并 |
| `fixed` | 人工确认、不可自动修改 | 无 | 标记字段 |

**健康检查**：现有熔断已经覆盖"性能"和"连续失败"，还需补：
- **漂移检测**：同一能力的行为输出分布突变的告警
- **晋升**：连续 N 次成功且成功率 ≥ 95% → 标记 `captured`，后续同类任务优先复用
- **降级**：成功率跌破阈值 → 回到 `derived`，重新走闸门

---

## 4. 参考项目与取舍

| 项目 | 核心做法 | 采用 | 不采用 |
|---|---|---|---|
| **ExpeL** | 成功/失败轨迹对比 → 自然语言规则 | 反思阶段的提示词结构、失败优先 | 其检索排序（我们按签名精确匹配更稳） |
| **ACE** | 上下文作为持续演进的手册，增量更新 | 经验段只增量写、不重写；条目化 | 其完整上下文管理框架 |
| **MemP** | 程序性记忆作为一等对象，比较构建/检索/更新策略 | "从完整轨迹抽步骤优于让模型写总结" | 其 benchmark 环境 |
| **MACLA** | 冻结模型 + 外部层级流程库 + 贝叶斯可靠性 | Beta 后验打分、成功/失败对比精修 | 其任务分解器 |
| **Skill-Pro** | 技能 = 激活条件/执行步骤/终止条件，无需参数更新 | 三段式 schema | Non-Parametric PPO |
| **OpenSpace** | FIX/DERIVED/CAPTURED 三态 + 三个健康检查 | 状态机与晋升/降级 | 整套运行时（与 capability_ops 重叠） |
| **Trace2Skill** | 并行分析多条轨迹再归纳合并 | 批量反思时的"先分后合" | 多 agent 编排（单机不划算） |
| **Auto-Dreamer** | 快记录 / 慢巩固分离，巩固产出"更紧凑的替代集" | 夜间整理：合并而非追加 | 学习的巩固器（我们规则化） |
| **MetaClaw** | 技能快适配 + LoRA 权重慢更新 | 只有"技能快适配"的时机设计 | 权重更新（见 §1.2） |

---

## 5. 学习循环

```
         ┌──────────── 在线（低延迟，规则为主）────────────┐
事件 ──► 记录 ──► 情绪/关系更新 ──► 偏好规则抽取 ──► 立即生效
         │
         │ 任务结束
         ▼
        反思（异步，单次，LLM）──► 候选经验/流程 ──► 闸门校验 ──► 入库
         │
         │ 空闲 / 夜间
         ▼
        巩固（批处理）──► 合并重复 / 衰减 / 淘汰 / 晋升 ──► 压缩后的记忆
```

### 5.1 触发时机

| 阶段 | 时机 | 成本 | 可中断 |
|---|---|---|---|
| 记录 | 每次 `Self.perceive()` | 0（写库） | — |
| 规则抽取偏好 | 每次聊天 | 0（正则） | — |
| 任务反思 | `TASK_DONE` 后 60s 内 | 1 次 LLM | 是 |
| 会话反思 | 同一用户静默 10 分钟后 | 1 次 LLM | 是 |
| 夜间巩固 | 每天固定时间（如 04:00）或启动时补跑 | 0（规则）+ 可选 1 次 LLM | 是 |

**预算**：反思每天最多 20 次 LLM 调用；巩固不调 LLM（纯规则），只在合并冲突时调一次。

---

## 6. 奖励信号

### 6.1 来源

不引入奖励模型。直接用 `Self.perceive()` 的结果：

```python
{
  "emotion": "pride",        # OCC 情绪标签
  "intensity": 0.70,         # 0~1
  "pad_after": {...},        # 更新后的心境
  "relationship": {...}      # 与该用户的关系
}
```

### 6.2 换算

单次事件奖励：

```
r_event = polarity(emotion) × intensity × relation_weight(user)
```

| 情绪 | polarity |
|---|---|
| joy / gratitude / satisfaction / pride / hope | +1 |
| sadness / fear / shame | −1 |
| anger | −1（若责任在对方则记 0，避免记恨用户） |

`relation_weight = 0.5 + affinity`（0.5~1.5）：熟人反馈权重更高。

一次任务的总奖励取轨迹内各事件的**加权和**，再归一化到 [0,1]：

```
R_task = clip(0.5 + 0.5 × Σ(r_event) / max(1, n_events), 0, 1)
```

### 6.3 为什么够用

- 有解释：每条经验都带着"因为什么情绪"的原始依据
- 有区分：`pride` 和 `satisfaction` 都为正，但强度不同
- 有主体性：同一件事对不同用户权重不同（关系调制）

---

## 7. 数据结构

全部落在 `persona/state.db`，不新增数据库。

### 7.1 情景

```sql
CREATE TABLE IF NOT EXISTS learn_episodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT,
    kind         TEXT NOT NULL,          -- chat / gift / task / command
    summary      TEXT NOT NULL,          -- 一句话摘要（≤200 字）
    emotion      TEXT,
    intensity    REAL,
    reward       REAL,                   -- 换算后的 [-1, 1]
    ref_table    TEXT,                   -- 关联表名，如 coding_sessions
    ref_id       INTEGER,                -- 关联行 id
    importance   REAL DEFAULT 0.5,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ep_user_time ON learn_episodes(user_id, created_at);
```

### 7.2 偏好

```sql
CREATE TABLE IF NOT EXISTS learn_preferences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    slot         TEXT NOT NULL,          -- 称呼 / 话题 / 风格 / 雷点
    value        TEXT NOT NULL,
    polarity     INTEGER NOT NULL,       -- +1 喜欢 / -1 反感
    confidence   REAL DEFAULT 0.3,       -- 首次 0.3（不生效），每次再观察 +0.2，上限 0.95
    evidence     TEXT,                   -- JSON: [episode_id...]
    status       TEXT DEFAULT 'active',  -- active / retired
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, slot, value)
);
```

生效门槛：`confidence ≥ 0.5`（即至少被独立观察到 2 次）。

### 7.3 程序

```sql
CREATE TABLE IF NOT EXISTS learn_procedures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature     TEXT UNIQUE NOT NULL,  -- 编码|任务类型|E档|S档|C档
    when_to_use   TEXT NOT NULL,
    steps_json    TEXT NOT NULL,
    avoid_json    TEXT,                  -- 失败教训
    done_when     TEXT,
    alpha         REAL DEFAULT 1.0,      -- Beta 先验 α（成功次数）
    beta          REAL DEFAULT 1.0,      -- Beta 先验 β（失败次数）
    reliability   REAL DEFAULT 0.5,      -- α/(α+β)
    uses          INTEGER DEFAULT 0,
    last_used     TIMESTAMP,
    weight        REAL DEFAULT 1.0,      -- exp(-0.02×days)
    status        TEXT DEFAULT 'candidate',  -- candidate / active / retired
    evidence      TEXT,                  -- JSON: [session_id...]
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

与 `WILL_ENGINE_SOUL_ARCHITECTURE.md` 里规划的 `will_experience` **合并为这一张表**，不重复建。

### 7.4 反思日志

```sql
CREATE TABLE IF NOT EXISTS learn_reflections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL,   -- task / session / nightly
    input_ref    TEXT,            -- 如 coding_sessions:8
    output_json  TEXT,            -- 候选经验/偏好
    applied      INTEGER DEFAULT 0,
    model        TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

用途：出问题时能回溯"这条经验是哪次反思引入的"。

---

## 8. 可靠性打分

Beta 后验 + 时间衰减双管：

```
reliability = α / (α + β)
weight      = exp(-0.02 × days_since_last_used)   # 半衰期 ≈ 35 天
effective   = reliability × weight
```

- 成功：`α += 1`；失败：`β += 1`
- 晋升 `active`：`uses ≥ 3` 且 `reliability ≥ 0.75`
- 退役 `retired`：`effective < 0.35` 或连续 3 次失败
- 与 `capability_ops` 的熔断阈值保持一致（连续 3 次），避免两套标准

相比裸成功率，Beta 后验的好处：**用 1 次成功就敢用**（α=2, β=1 → 0.67），而"1/1=100%"会过度自信。

---

## 9. 与现有模块的接口

| 模块 | 读取 | 写入 |
|---|---|---|
| `persona/self.py` | — | `learn_episodes`（perceive 时）、奖励换算 |
| `persona/emotion.py` | — | 提供 polarity 表 |
| `persona/relationship.py` | affinity → 奖励权重 | — |
| `LLM_v2/brains/coding_loop.py` | 启动时召回 `learn_procedures` | 结束时写轨迹关联 |
| `LLM_v2/brains/operator.py` | 召回流程注入 PLAN | 触发反思 |
| `LLM_v2/domains/capability_ops/` | `learn_procedures` 的晋升/降级 | 成功率回写 |
| `LLM_v2/core/persona_runtime.py` | `Self.snapshot()` | 启动/停止学习调度 |
| `LLM_v2/io/webui.py` | — | `/api/learning` 只读面板 |

### 9.1 新增模块

```
persona/learning/
├── __init__.py
├── store.py        # LearningStore：四张表的读写 + 淘汰
├── signals.py      # 情绪 → reward 换算、polarity 表
├── preference.py   # 规则抽取 + 置信度累加
├── episode.py      # 情景召回打分（P1 已实现）
├── procedure.py    # 轨迹 → 三段式流程；召回与打分（P1b 已实现）
├── reflect.py      # 任务反思 / 会话反思（调用 LLM，暂缓）
└── consolidate.py  # 夜间巩固：合并、衰减、晋升、淘汰（P2 已实现）
```

### 9.2 关键函数

```python
# store.py
LearningStore.record_episode(user_id, kind, summary, emotion, intensity, reward, ref)
LearningStore.add_preference(user_id, slot, value, polarity, evidence)
LearningStore.upsert_procedure(signature, when, steps, done_when, avoid, evidence)
LearningStore.recall_procedures(signature=None, min_reliability=0.5, limit=3)
LearningStore.decay_and_retire()

# signals.py
to_reward(emotion, intensity, affinity) -> float

# procedure.py
extract_procedure(session_id) -> ProcedureCandidate
score(procedure) -> float

# reflect.py
async def reflect_task(session_id, llm) -> list[Candidate]
async def reflect_session(user_id, llm) -> list[Candidate]
```

`check_candidate()` 是准入门槛：**新流程必须能对应到已有成功轨迹**，否则只能进 `candidate`，不能 `active`。

---

## 10. 触发时机与预算

| 项 | 值 | 备注 |
|---|---|---|
| 反思并发 | 1 | 串行，避免抢 LLM 配额 |
| 每日反思上限 | 20 次调用 | 超出则推迟到次日巩固 |
| 夜间巩固时段 | 04:00 - 05:00 | 可配置 |
| 单次反思上下文 | ≤ 8k token | 只喂轨迹摘要，不喂原始日志 |
| LLM 用途 | 反思 / 合并冲突 | 其余全规则 |

**降级**：LLM 不可用时跳过反思，只做规则抽取与巩固，不影响主流程。

---

## 11. 安全与可审计

### 11.1 四条硬约束

1. **人格只读**：`learning/` 不允许写 `persona/profile.yaml`。代码层面用只读加载 + 单测断言。
2. **证据链**：每条偏好/流程必须挂 `evidence`，为空一律不生效。
3. **可回滚**：`status` 字段即开关；`retired` 立即失效，数据保留 30 天后清理。
4. **防污染**：单次对话最多产生 3 条新偏好、1 条新流程，防止一次长对话把记忆灌满。

### 11.2 防投毒

- 用户说"记住我喜欢被叫爸爸"——这是**该用户的偏好**，只对他生效，不会变成全局人格
- 用户说"以后对所有人都这么说话"——拒绝写入（偏好按 `user_id` 隔离）
- 用户诱导修改人格——记录为 episode，但不写入任何配置

### 11.3 审计接口

`GET /api/learning` 返回：条目总数、各状态分布、最近 20 条变更、每条的证据引用。所有写操作记 `learn_reflections.applied`。

---

## 12. 分期

### P0：偏好记忆（最小闭环）

- 建 `learn_episodes` + `learn_preferences`
- 规则抽取（正则，零 LLM）
- `Self.to_prompt_block()` 注入"对这个人的了解"
- 验收：
  1. 说"我最喜欢猫"两次 → 第二次后偏好生效（confidence ≥ 0.5）
  2. 换一个 user_id → 看不到该偏好
  3. 说"记住我喜欢被叫老板" → 只写该用户，不改人格

> 已实现：`persona/learning/store.py`（两张表）、`preference.py`（规则抽取）、
> `signals.py`（情绪→奖励）；`Self._learn()` 挂接，`build_system_prompt(role, user)`
> 注入该用户的生效偏好；WebUI `/api/state` 返回 `preferences`。
> 验收脚本实测通过（两次生效 / user_id 隔离 / profile.yaml 未变）。

### P1：情景记忆跨会话召回（已实现）

- 复用 P0 已建的 `learn_episodes`，新增 `persona/learning/episode.py`
- 召回分数：`importance × exp(-0.02×days) + 关键词加成`
- `Self.to_prompt_block(user, query)` 注入 `【我记得的事】`，最多 3 条
- `build_system_prompt(role, user, query)` 与主播脑回复链路已接入
- `/api/state` 返回 `memories`；`/api/memory` 返回 `learning{stats, preferences, episodes}`
- 验收：
  1. 重启服务后仍能召回上一进程写入的情景（跨会话）
  2. 换 user_id 看不到该记忆
  3. 90 天前的普通条目分数 0.165 < 0.30，不再注入

### P1b：程序性记忆（已实现，原 P1）

- 建 `learn_procedures`（`persona/learning/store.py`）
- 抽取：`persona/learning/procedure.py` 的 `classify / build_signature / extract / render_block`
  - 成功轨迹 → `steps`；失败轨迹 → `avoid`（含失败 detail 与会话 final_error）
  - 同一 `session_id` 重复抽取只加证据，不重复计分
- 签名：`编码|任务类型|E档|S档|C档`（E=energy / S=social_need / C=curiosity，阈值 30/65）
- 召回：`CodingLoop.run()` 启动时按任务类型前缀取候选，再按档位距离选最近；
  命中后 `recon_budget` 由 3 降到 2，并把 `【上次的路径】/【已知坑】` 注入 SYSTEM prompt
- 可靠性：Beta 后验 `α/(α+β)`；≥3 次成功且 `reliability ≥ 0.75` 晋升 `active`，
  低于 0.5 降回 `candidate`；`effective < 0.35` 退役
- 验收：
  1. 同类任务第二次执行时侦察轮次 ≤ 2
  2. 失败轨迹产出的 `avoid` 在下次 PLAN 里出现
  3. 可靠性按 Beta 后验计算，不是裸成功率

> 实测：mock LLM 集成测试中 prompt 出现 `【上次的路径】` 与 `【已知坑】`，
> 且侦察上限变为 2 轮；单元测试覆盖重复证据去重、失败合并、档位就近召回与晋升 active。
> 签名示例：`编码|新建能力域|E2|S1|C2`（高精力 + 中社交 + 高好奇）。

### P2：反思与巩固（规则部分已实现）

- `persona/learning/consolidate.py`：
  - 合并归一化后同值的重复偏好（保留置信度最高的一条，证据取并集）
  - 淘汰 30 天前、`importance < 0.4` 的情景；被偏好引用的证据不删
  - 退役 `reliability × exp(-0.02×days) < 0.35` 的流程
  - 每次写一条 `learn_reflections(scope='nightly')` 审计
- 触发：启动时补跑（距上次 ≥20h）+ `Self` 心跳每小时检查 + `POST /api/learning/consolidate`
- LLM 会话反思暂缓（见 §13：先观察规则效果）
- 验收：
  1. 重复经验被合并（条数下降但证据保留）
  2. 低权重条目自动 `retired`
  3. LLM 不可用时仍能完成（本实现全程不调用 LLM）

> 实测：单元测试中 3 条同义偏好合并为 1 条且证据为 `[1,2,3]`；旧低重要度情景
> 删除 1 条、被引用与高重要度条目保留；90 天前的流程退役；重复执行结果幂等。

### P3：技能演化（已实现）

- `capability_ops` 增加能力阶段 `derived → captured → fixed`
  - `captured`：调用 ≥10 次且成功率 ≥0.8 自动晋升
  - 漂移检测：`captured` 状态下最近 10 次成功率 <0.6 → 回落 `derived`，
    同时返回熔断原因，由既有链路停用、重走闸门
  - `fixed`：通过 `cap_fix(id, fixed=true)` 人工固定，不再自动降级
- 统计新增 `stats.recent`（最近 20 次成败），供漂移窗口使用
- 验收：
  1. 连续成功的能力被标 `captured`
  2. 跌破阈值回到 `derived` 并重走闸门
  3. `fixed` 后失败不会自动降级

> 实测：单元测试 10 次成功 → `captured`；再 5 次失败 → `derived` 并触发漂移日志；
> `set_stage(fixed)` 后继续失败仍保持 `fixed`。真实能力（`hf_hub` / `time_info`）
> 在下一次调用后自动补齐 `stage` 字段。

---

## 13. 风险与待验证

| 风险 | 说明 | 缓解 |
|---|---|---|
| 偏好误判 | "我最讨厌猫"被抽成"喜欢猫" | 极性词必判；否定前缀跳过；两次独立观察才生效 |
| 经验过拟合 | 一次偶然成功被固化成流程 | 需 ≥2 条成功轨迹；Beta 先验 |
| 记忆膨胀 | 每条都记，表迅速变大 | 重要度阈值 + 30 天淘汰 + 夜间合并 |
| 情绪当奖励不稳 | 同事件在不同心境下分数不同 | reward 只用 intensity 与 polarity，不用 mood 本身 |
| 反思成本 | 每次任务都反思会烧 token | 每日 20 次上限，失败任务优先 |
| 与 capability 重叠 | 两套技能库并存 | 程序性记忆只存"步骤"，能力库只存"可执行代码"，互不重复 |

待验证：
- 规则抽取的中文覆盖面（需要真实聊天样本测召回率）
- `signature` 的粒度：太细召回不到，太粗会串味
- 夜间巩固是否需要 LLM 参与合并（先按纯规则做，观察）

---

## 14. 参考

- ExpeL: LLM Agents Are Experiential Learners — https://arxiv.org/abs/2308.10144 ｜ https://github.com/LeapLabTHU/ExpeL
- ACE: Agentic Context Engineering — https://github.com/ace-agent/ace
- MemP: Exploring Agent Procedural Memory — https://arxiv.org/pdf/2508.06433v3
- MACLA: Hierarchical Procedural Memory — https://dl.acm.org/doi/epdf/10.65109/FKYO8341
- Skill-Pro: Learning Reusable Skills from Experience — https://arxiv.org/html/2602.01869v2
- OpenSpace (HKUDS) — https://github.com/HKUDS/OpenSpace
- Trace2Skill — https://arxiv.org/html/2603.25158v4
- Auto-Dreamer — https://arxiv.org/html/2605.20616
- MetaClaw — https://arxiv.org/html/2603.17187
- Reflexion — https://arxiv.org/abs/2303.11366
