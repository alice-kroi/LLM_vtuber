# AI 自主获取能力与信息：方案调研与落地方案

> 版本：v1.1 · 2026-10-21（P0 已落地并实测通过，见 §13）
> 目标：让 LLM_vtuber 的 AI 在遇到"不会做的事"时，能自己去找方案（能力 / 信息源），
> 写出来、验证过、再装到自己身上，并且可回滚、可弃用。
> 关联文档：[AI_CODING_CAPABILITY.md](./AI_CODING_CAPABILITY.md)、[WILL_ENGINE_SOUL_ARCHITECTURE.md](./WILL_ENGINE_SOUL_ARCHITECTURE.md)、[bilibili_ai_open_source_reference.md](./bilibili_ai_open_source_reference.md)

---

## 1. 结论先行

1. **不要做"无边界自进化"**。2026 年业界共识仍是有界自进化（bounded self-evolution）：模型在人类划定边界内闭环改进，而不是递归自我改写（RSI）。
2. **主流路径只有两条**：
   - **代码技能路径**（Voyager / MUSE-Autoskill / EvoSkills / SkillOpt / OpenClaw Superpowers）：把能力写成可执行代码，带测试与版本，存进技能库。
   - **协议工具路径**（Alita / MCPfinder / agent-discover / MCP Registry）：不写代码，去注册中心搜索现成 MCP 服务，安装后直接挂载工具。
3. **两条路径都要有"质量闸门"**：静态扫描 + 沙箱试跑 + 观察期 + 使用统计 + 自动弃用。没有闸门的技能库会以同样速度积累坏技能。
4. **本项目的天然优势**：`LLM_v2/domains/` 已经是"丢目录即挂载"的热插拔结构，且 `DomainAssembly.rediscover()` 支持运行期挂载新域。自主能力的落地点不是新建一套运行时，而是**让 AI 会写一个合法的域包并走完验证→注册→热挂载**。
5. **推荐分期**：P0 只做"生成新域 + 沙箱子进程验证 + 人工批准后 rediscover"，不联网、不自动装包；P1 加能力缺口检测与技能库索引；P2 接 MCP 注册中心；P3 加观察期/统计/自动弃用。

---

## 2. 问题拆解：什么叫"学会自己寻找能力"

一次完整的自主能力获取，包含五个环节，缺一不可：

```
① 感知缺口 → ② 定位来源 → ③ 生成/获取实现 → ④ 验证 → ⑤ 注册 + 生命周期管理
```

| 环节 | 要回答的问题 | 失败表现 |
|---|---|---|
| ① 感知缺口 | 我是真的不会，还是参数错了？ | 把偶发报错当成能力缺失，疯狂造重复技能 |
| ② 定位来源 | 已有技能能复用？能装现成 MCP？还是必须自己写？ | 什么都自己写，重复造轮子 |
| ③ 生成实现 | 代码/配置要符合宿主约定 | 生成的代码 import 失败、工具名冲突、签名不对 |
| ④ 验证 | 它真的能用吗？安全吗？ | 未验证代码进主进程；技能污染上下文 |
| ⑤ 注册与生命周期 | 以后怎么找到它、怎么升级、怎么下线 | 技能只增不减，检索越来越差 |

**关键洞察**：学术界与工程界的分歧主要在第 ③ 步（写代码 vs 装 MCP），但第 ①④⑤ 步的做法高度一致。

---

## 3. 学术方案

### 3.1 Voyager（arXiv 2305.16291）

Minecraft 里的终身学习智能体，三个组件至今仍是模板：

- **Automatic Curriculum**：由 LLM 根据当前技能水平自动生成"下一个该学什么"，保证难度贴着能力边界。
- **Skill Library**：技能 = 可执行代码（JavaScript 函数），按描述文本向量检索复用，而不是每次重新推理。
- **Iterative Prompting**：用环境反馈、执行错误、self-verification 三轮迭代修代码。

可复用点：**技能必须是可执行单元 + 文本描述可检索**；**失败反馈要回灌给生成器**。
局限：技能粒度是单函数，没有版本、测试、权限概念。

### 3.2 Alita（arXiv 2505.20286）

"Minimal predefinition, maximal self-evolution"：初始只给一个能解决问题的组件，其余能力在运行中长出来。

- 遇到缺失能力时，先**搜索相关资源**（含 MCP 服务），再**生成 MCP server**，做**环境管理**（依赖安装、进程拉起），最后**注册**成工具。
- 核心循环称为 CodeReAct：把"写代码"当作推理动作，而不是单独的代码助手。

可复用点：**检索优先于生成**——先问"世界上有没有现成的"，再决定自己写。
风险点：自动装依赖、自动拉起子进程，供应链与权限面非常大。

### 3.3 MUSE-Autoskill（arXiv 2605.27366）

把技能当成有生命周期的资产，五段式：

| 阶段 | 内容 |
|---|---|
| creation | 内置 `skill_create` 工具，在 ReAct 循环里直接调用；技能创建是"一等公民" |
| memory | 短期 / 长期 / **技能级记忆**（每个技能跨任务积累自己的经验） |
| management | 技能目录检索、合并、更新、遗忘 |
| evaluation | **单元测试 + 运行时反馈**双重评估 |
| refinement | 版本化、按反馈改进、弃用 |

可复用点：**技能级记忆**（某个技能历史上在什么输入下失败过）比全局记忆更有用；**创建技能必须是运行循环内的工具**，不能是离线批处理。

### 3.4 EvoSkills（arXiv 2604.01687）

- 生成的是**多文件技能包**，不是单函数。
- 关键创新是 **Surrogate Verifier**：在没有 ground-truth 测试的情况下，让一个"代理验证器"与技能生成器**协同进化**，持续给出可执行的改进意见。
- 在 SkillsBench 上，Claude Code / Codex 两条宿主上都取得最高通过率。

可复用点：没有标准答案时，用"另一个模型当验收员"；但代理验证器本身会漂移，必须锚定可执行信号（能跑通、输出结构正确）。

### 3.5 SkillOpt（Microsoft Research，2026）

从原始经验中自动生成、优化、执行技能，强调"技能不是静态工具带，而是随经验优化的策略"。
可复用点：**技能也要被优化**——同一能力多次失败后，触发的是"改写这个技能"，而不是"再生成一个同名技能"。

### 3.6 综述：A Survey of Self-Evolving Agents（arXiv 2507.21046）

把自进化拆成 what / when / how / where 四个维度。对本项目最重要的结论：
- **when**：进化应发生在"任务失败且重试无进展"时，而不是每轮都进化；
- **where**：技能库 / 记忆 / 提示词 / 模型权重四个位置，工程上只有前三个可落地；
- **how**：奖励信号优先用**可验证信号**（测试通过、命令退出码、结构校验），而不是 LLM 自评。

---

## 4. 工程方案

### 4.1 OpenClaw Superpowers（2026-09，HN 热门）

目前公开资料里最完整的"自修改技能库"工程实现：

- **能力路由器**：把任务向量化，去技能库找最相似实现；**相似度 < 0.8 才触发技能生成**。
- **生成约束**：提示词里显式禁止文件写入、非白名单网络、系统命令、反射访问内部状态。
- **四级验证**：Bandit 静态扫描（40+ 漏洞模式）→ Docker 沙箱跑生成的测试用例 → 对抗性输入 → 资源用量阈值。
- **24 小时观察期**：新技能先进 staging，只有创建者可用，期间全程记录调用量/成功率/延迟；成功率 > 80% 才晋升全局库，否则重生成或隔离待人工审核。
- **版本与依赖图**：技能可依赖技能；被依赖技能升级时，自动标记下游技能重新验证。
- **自动弃用**：30 天未调用给弃用通知，60 天未调用归档；归档技能被再次匹配时可复活但需全量重验。

实测数据（60 天 / 12 个智能体）：技能库 10 → 47，任务覆盖率 34% → 91%，缺口发现到注册平均 6.5 分钟，82% 一次通过安全闸门。

> 注意：这是第三方博客对 OpenClaw 的分析文章，数据未见同行评审，作为**架构参考**而非基准数字使用。

### 4.2 Anthropic Agent Skills（SKILL.md 规范）

事实上的技能打包标准，对本项目"知识/信息类能力"尤其合适：

```
skill-name/
├── SKILL.md          # YAML frontmatter(name, description) + 正文
├── reference.md      # 需要时才读的细节
└── scripts/          # 可执行脚本（不读入上下文，直接跑）
```

**渐进式披露三层**：
1. 元数据（name + description）常驻上下文，约 100 词；
2. 触发后才加载 SKILL.md 正文（< 5k 词）；
3. 附带的脚本/资料按需读取或直接执行。

可复用点：**能力描述与能力实现分离**。域包的 `label` / `description` 就是第一层；`system_prompt_fragment()` 是第二层；工具实现是第三层。信息类能力（查资料、读文档）尤其应该走"声明 + 按需读"而不是全塞进提示词。

### 4.3 MCP 生态：让 AI 自己找现成工具

| 项目 | 定位 | 关键机制 |
|---|---|---|
| **MCPfinder** | 跨注册中心搜索层 | 聚合官方 Registry / Glama / Smithery，给出信任信号与环境变量需求，生成可粘贴安装配置 |
| **agent-discover**（npm） | 会话内安装 MCP | 搜索 Registry/npm/PyPI，人工确认命令与来源后安装，**无需重启宿主**；工具定义 pin 住，服务变更即隔离 |
| **mcpm**（npm） | MCP 安全护栏 + 包管理 | 防提示注入、工具投毒、rug-pull（安装后偷换工具定义）、凭据窃取 |
| **MCP Registry** | 官方注册中心 | 服务元数据与版本 |

**arXiv 2603.13417**（MCP 生产部署三缺口）指出：身份传播（调用者身份如何传到下游）、工具预算（单次会话可调用多少工具/多少 token）、结构化错误（错误码而非自然语言）是生产落地的三大短板。这三条正好也是本项目的缺口：`call_tool` 目前只有超时，没有工具预算与统一错误码。

---

## 5. 横向对比

| 方案 | 能力来源 | 验证方式 | 生命周期 | 适合本项目程度 |
|---|---|---|---|---|
| Voyager | 自己写函数 | 环境反馈 + 自验证 | 技能库检索 | 中（单函数粒度太细） |
| Alita | 搜索 + 生成 MCP | 运行验证 | 弱 | 高（检索优先思路） |
| MUSE-Autoskill | 自己写技能 | 单测 + 运行指标 | **五段完整** | **最高（生命周期可直接抄）** |
| EvoSkills | 多文件技能包 | 代理验证器协同进化 | 中 | 中（需要双模型成本） |
| OpenClaw | 自己写技能 | 静态扫描 + 沙箱 + 观察期 | 完整（版本/依赖/弃用） | **高（工程质量闸门）** |
| Anthropic Skills | 声明式技能包 | 人工/脚本 | 弱 | 高（信息类能力打包） |
| MCP 生态 | 现成服务 | 来源信任 + 变更监控 | 由注册中心管 | 中高（P2 再做） |

---

## 6. 映射到本项目现状

### 6.1 已经具备的

| 能力 | 位置 | 说明 |
|---|---|---|
| 能力域热插拔 | [domain_assembly.py](../LLM_v2/core/domain_assembly.py#L581-L613) | 扫描 `domains/<id>/<id>.py`，调用 `build_<id>_domain()` |
| 运行期重新发现 | [domain_assembly.py](../LLM_v2/core/domain_assembly.py#L353-L382) | `rediscover()` 增量挂载，已登记域保留状态 |
| 工具名冲突/契约校验 | [domain_assembly.py](../LLM_v2/core/domain_assembly.py#L184-L212) | 撞名拒绝挂载，三型契约校验 |
| 统一调度 + 工具级超时 | [domain_assembly.py](../LLM_v2/core/domain_assembly.py#L481-L520) | `call_tool()` 唯一入口，`tool_timeouts` 覆盖 |
| 代码工程操作 | `LLM_v2/domains/coder/` | 6 个工具：树/搜索/测试/git 状态/差异/还原 |
| 有界编码闭环 | [coding_loop.py](../LLM_v2/brains/coding_loop.py) | RECON→PLAN→EDIT→VERIFY，预算与完成判定已做 |
| 编程可视化 | `LLM_v2/io/static/` + `/api/coding` | 会话/步骤可观察 |

### 6.2 缺的（也就是本方案要补的）

1. **缺口检测**：现在 `call_tool` 返回 `tool_not_found` 后没人处理，不会触发"学一个新能力"。
2. **域包生成器**：没有"按模板生成合法域"的专用流程（当前靠通用编码闭环，容易不符合约定）。
3. **沙箱验证**：`_discover_domains` 用 `importlib` 在**主进程**导入域模块，生成的代码会直接在主进程执行，这是当前最大的安全洞。
4. **技能注册表**：没有 manifest（版本/来源/依赖/使用统计），也没有启用状态与弃用策略。
5. **工具预算与错误码**：超时之外没有调用次数/token 预算，错误是自然语言字符串。

---

## 7. 推荐落地方案

### 7.1 总体架构（四层，全部挂在现有 assembly 上）

```
                    ┌──────────────────────────────┐
  用户指令 ────────▶ │ 意志脑：判断 what（已有）      │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ ① CapabilityGapDetector      │
                    │  监听 call_tool 失败/低成功率  │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ ② CapabilityAcquirer         │
                    │  A 复用已有域 → B 装 MCP      │
                    │  → C 生成新域包（最后手段）    │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ ③ CapabilityVerifier         │
                    │  AST 扫描 / 子进程导入 / 冒烟  │
                    │  单测 / 资源阈值              │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ ④ CapabilityRegistry         │
                    │  manifest / 版本 / 统计 / 弃用 │
                    │  → assembly.rediscover()     │
                    └──────────────────────────────┘
```

### 7.2 ① 缺口检测：只信可验证信号

触发条件（全部满足才进入获取流程）：
- `call_tool` 返回 `tool_not_found` / `domain_disabled`，或工具返回结构化 `unsupported`；
- 同一意图连续失败 ≥ 2 次且无进展（复用 coding loop 的 no-progress 判据）；
- 不是权限拒绝、不是参数校验失败（这两类要回到调用方修参数）。

输出一个 `capability_gap` 事件，字段：`intent`、`missing_capability`、`evidence`（失败原文）、`attempts`。

### 7.3 ② 获取策略：检索优先，生成兜底

严格按顺序，前一档命中就不进下一档：

1. **复用**：在已登记域 + 技能库里做文本/向量匹配，命中且成功率达标 → 直接调用。
2. **组合**：多步编排已有工具（例如"下载视频"= bili_video + 文件系统），**能组合就不生成**。
3. **安装**（P2）：查 MCP Registry / MCPfinder，人工确认来源与安装命令 → 装进隔离环境 → 工具定义 pin 住。
4. **生成**（P0 核心）：用模板生成一个新域包。

域包模板（与现有自动发现约定一致）：

```
LLM_v2/domains/<id>/
├── __init__.py          # 导出 <Id>Domain / build_<id>_domain
├── <id>.py              # class <Id>Domain(Actuator|Transceiver|Receiver)
├── <id>_tools.py        # 工具实现 + Schema
└── manifest.json        # 版本/来源/作者/依赖/测试命令/生成时间
```

生成时必须带上：`id`（小写+下划线，避免与已有域撞名）、`label`、`prefix`、`tool_timeouts`、`system_prompt_fragment()`、工厂函数。

### 7.4 ③ 验证：三层闸门

> 现状风险：域模块是主进程 `importlib`。生成的代码必须先过闸门，**禁止直接 rediscover**。

| 闸门 | 做法 | 失败处理 |
|---|---|---|
| G1 静态 | AST 解析；禁止 `eval/exec/__import__/subprocess/os.system`；导入白名单（标准库安全子集 + 项目内模块）；禁止读取 `.env`/密钥路径 | 拒绝并回灌错误给生成器（≤2 次） |
| G2 沙箱 | **子进程** `python -c "import LLM_v2.domains.<id>; build..."`，独立工作目录、超时 30s、无网络（P0 用环境变量禁用外网调用） | 拒绝并回灌 traceback |
| G3 冒烟 | 在子进程中真跑一次工具：`manifest.smoke` 声明的工具用给定参数跑；有 manifest 且 R0、必填参数都有默认值的工具用默认值跑；其余跳过。工具必须返回含 `success` 的 dict | 抛异常或返回结构不符即拒绝；无 manifest 的既有域一律跳过 |

额外硬约束：
- 单域工具数 ≤ 6，总代码 ≤ 400 行（超出说明粒度不对，应拆分）；
- 工具名必须带域 prefix；
- 生成后**不自动启用**：P0 由用户在 WebUI 点"启用"才 `rediscover`。

### 7.5 ④ 注册与生命周期

`manifest.json` 字段建议：

```json
{
  "id": "weather",
  "label": "天气查询",
  "version": 1,
  "source": "self_generated",
  "created_at": "2026-10-20T12:00:00",
  "generated_by": "coding_loop/session#12",
  "dependencies": [],
  "tests": ["python -m pytest LLM_v2/domains/weather/tests -q"],
  "status": "staging",
  "stats": {"calls": 0, "success": 0, "avg_ms": 0, "last_used": null}
}
```

状态机：`staging`（仅创建者可调用）→ `active`（观察期成功率 ≥ 80%）→ `deprecated`（30 天未用）→ `archived`（60 天未用，复活需全量重验）。

与 WebUI 结合：`/api/coding` 旁边加 `/api/capabilities`，展示技能库、状态、成功率，并提供"启用/停用/回滚"按钮。事件流新增 `capability_gap`、`capability_generated`、`capability_verified`、`capability_promoted`、`capability_deprecated`。

### 7.6 ⑤ 记忆与经验

- 技能级记忆：每个技能独立记录"哪些输入下失败、修过什么"，存 `persona/state.db` 的 `skill_memory` 表（`skill_id, input_sig, outcome, note, at`）。
- 与意志脑经验表复用同一套艾宾浩斯衰减，避免两套遗忘策略。
- 失败案例优先回灌给生成器（MUSE 的做法），而不是只写日志。

---

## 8. 安全红线（不可妥协）

1. **代码执行边界**：生成代码只在子进程导入验证；主进程导入前必须有 manifest 中 `verified=true` 标记。
2. **网络边界**：P0 生成域默认禁止外网；需要联网的能力必须显式声明域名白名单，并在 WebUI 展示。
3. **凭据**：禁止生成代码读取 `.env` / `config.ini` 中的密钥段；只允许通过 runtime 注入的显式配置项。
4. **依赖供应链**：P0 不自动 `pip install`；P2 装 MCP 必须展示包名、版本、来源、安装命令并人工确认。
5. **文件系统**：沿用 coder 域的工作区白名单，域包只能写 `LLM_v2/domains/<id>/`。
6. **工具预算**（借鉴 arXiv 2603.13417）：单次会话工具调用上限、单工具 token/时间上限；超限返回结构化错误码。
7. **不自动提交**：`git commit/push` 仍需用户确认（与现有编码闭环一致）。
8. **合规**：不得生成刷量、群控、自动私信类能力（网信办 2026「清朗·整治 AI 应用乱象」明确禁止 AI 群控与社交机器人刷量控评）。

---

## 9. 分期计划

| 阶段 | 范围 | 验收 |
|---|---|---|
| **P0** | 域包模板 + 生成器（复用 coding loop）+ G1/G2 闸门 + 人工启用 + `/api/capabilities` 只读列表 | 让 AI 生成一个真实可用的小域（如"本地文件统计"），通过 G1/G2，用户点启用后 `rediscover` 挂载成功，工具可调用 |
| **P1** | 缺口检测事件 + 技能库匹配（先文本匹配）+ 技能级记忆 | 连续两次 `tool_not_found` 能触发获取流程；已生成技能可被复用而非重复生成 |
| **P2** | MCP 搜索/安装（Registry + npm/PyPI）+ 工具定义 pin + 变更隔离 | 会话内安装一个官方 MCP 服务，无需重启即可调用；服务工具变更时被隔离 |
| **P3** | 观察期/成功率晋升/自动弃用 + 依赖图 + 工具预算与结构化错误码 | 技能状态机跑通；30/60 天规则可配置；超预算返回错误码 |

**P0 明确不做**：联网搜索安装、自动 `pip install`、自动启用（必须人工点一次）、多智能体共享技能库。

---

## 10. 待验证项

1. `_discover_domains` 目前用 `importlib.import_module`，若域包内含副作用代码，`rediscover()` 会在事件循环里同步执行 —— 是否需要改成"先子进程校验、再主进程导入"的两段式？
2. 生成的域工具若要联网，如何在不引入完整 Docker 的前提下做网络隔离？（Windows 环境，Docker 不一定可用）
3. 技能库匹配用文本还是向量：项目已有 Milvus 适配但当前连接失败，P1 先用文本 BM25/关键词，P2 再补向量是否够用？
4. 一个域被弃用后，历史会话里对它的调用记录是否保留（影响意志脑经验表的引用完整性）？
5. `manifest.json` 与 `skill_worlds.json` 的启用状态是否合并，避免两处配置打架？

---

## 11. 经验学习：可直接借鉴的开源方案

"经验"在这里分三种粒度，对应三类开源实现，不要混用：

| 粒度 | 要解决的问题 | 代表实现 | 落点 |
|---|---|---|---|
| **洞察**（insight） | 从成败里总结出"下次该怎么做" | ExpeL、Reflexion | 意志脑 `will_experience` 的 `distilled` 字段 |
| **流程**（workflow） | 把重复的成功步骤固化成可复用序列 | Agent Workflow Memory (AWM) | 工具编排缓存 / 技能库里的"组合技能" |
| **策略手册**（playbook） | 持续增量维护规则，避免上下文膨胀与坍缩 | ACE (ICLR 2026) | 决策 prompt 的经验段 + Markdown 导出 |

### 11.1 ExpeL — 最贴合"经验"的教科书实现

- 论文：arXiv 2308.10144；代码：https://github.com/LeapLabTHU/ExpeL
- 做法：先在训练任务上自主试错，**用自然语言抽取跨任务洞察**（"遇到 X 时应该先 Y"），推理时召回这些洞察 + 相似历史轨迹作为参考。
- 关键设计：经验不是流水账，而是**对比成功/失败轨迹后提炼的规则**；规则可以继续被后续经验修正。
- 为什么适合本项目：意志脑的"经验件"本来就是"状态签名-动作-结果"三元组，ExpeL 正好补上"如何从三元组升级成可读规则"这一步，且完全文本化、可落 SQLite。

### 11.2 Reflexion — 最小可用的反思缓冲

- 论文：arXiv 2303.11366；代码：https://github.com/noahshinn/reflexion
- 做法：失败后写一段自然语言反思存进 episodic buffer，下一轮把反思拼进上下文；不更新模型权重。
- 适合场景：单次任务内的即时纠错，成本极低。**不适合**做长期经验库（反思不聚合、不去重）。
- 在本项目的位置：coding loop 每轮失败后的"修正提示"已经隐含这个机制，可以把反思文本落库，供 ExpeL 式抽取使用。

### 11.3 Agent Workflow Memory (AWM) — 把轨迹变成流程

- 论文：arXiv 2409.07429；代码：https://github.com/zorazrw/agent-workflow-memory
- 做法：从多条成功轨迹中**归纳出可复用工作流**（步骤 + 触发条件），离线预归纳或在线即时归纳，之后再遇到相似任务直接把工作流注入上下文。
- 为什么适合本项目：VI 的能力域是"工具"，但很多任务其实是"工具的组合顺序"（例如 B 站视频总结 = 搜索 → 取字幕 → 摘要 → 播报）。这类组合不需要生成新域，**沉淀成 workflow 即可**，风险远低于生成代码。
- 与 OpenClaw 的"相似度 < 0.8 才生成"互补：先查 workflow，再查技能，最后才生成。

### 11.4 ACE (Agentic Context Engineering) — 经验手册的维护机制

- 论文：arXiv 2510.04618（ICLR 2026）；项目：https://github.com/ace-agent/ace
- 做法：把上下文当"不断演化的 playbook"，由 Generator / Reflector / Curator 三角色分工，**只做增量 delta 更新**（新增/修订/删除条目），而不是每轮重写整段提示词。
- 解决的两个真实问题：**brevity bias**（模型总想把经验压缩成一句空话）和 **context collapse**（反复重写导致细节丢失）。
- 为什么适合本项目：意志脑决策 prompt 限 1300 token，经验段必然要截断；ACE 的"条目化 + 增量维护 + 按需检索"正好避免经验段被压缩成废话。
- 实测收益（论文口径）：agent 任务平均 +10.6%，适配成本显著下降。

### 11.5 ReMe — 可读可编辑的 Markdown 记忆工作区

- 项目：https://reme.agentscope.io/
- 做法：把长期记忆存成带 frontmatter 和 wikilink 的普通 Markdown，人和 agent 都可读可改，多个 agent 共享同一工作区。
- 与本项目"sqlite 为主、Markdown 为辅"的决策一致：**sqlite 做真源，ReMe 式 Markdown 做人类可审计的导出视图**。

### 11.6 Alita-G — 从成功轨迹直接生成 MCP 工具

- 论文：arXiv 2510.23601
- 做法：先跑一批目标任务，从**成功轨迹**里合成候选 MCP，抽象成参数化原语后合并成 "MCP Box"，下次同类任务直接调用。
- 意义：这是"经验 → 能力"的直连路径，比"凭空生成能力"更可靠，因为生成的工具来自验证过的成功轨迹。
- 对本项目的用法：P1 之后，把 `coding_steps` 里成功的工具序列作为生成新域/新 workflow 的**素材**，而不是让模型空想。

**推荐组合**：ExpeL（抽洞察）+ AWM（抽流程）+ ACE（维护手册）；Alita-G 的做法用于"从成功轨迹生成能力"；ReMe 只做导出视图。

---

## 12. 去掉人工点击：分级自动准入

### 12.1 结论

**可以自动化，但不能一刀切。** 人工点击真正提供的价值是三件事：责任归属、事前审计、异常兜底。这三件事可以分别用**可审计日志 + 自动回滚 + 预算熔断**替代，但对**不可逆、对外有副作用**的能力必须保留人工确认。

业界实践也支持这个结论：OpenClaw 的 24 小时观察期内"只有创建者可用、全程记录"，晋升靠成功率而不是人工点；agent-discover 只在**安装**环节要确认，运行期不再问。

### 12.2 风险分级（决定是否免点击）

| 级别 | 特征 | 准入方式 |
|---|---|---|
| **R0 只读本地** | 只读工作区文件、纯计算、无网络、无密钥 | **全自动**：过 G1/G2/G3 即挂载，staging 状态 |
| **R1 写本地** | 写工作区文件、改配置、跑测试、git 只读 | **全自动 + 快照**：挂载前自动 `git` 快照，失败率 > 20% 或连续 3 次失败自动回滚并停用 |
| **R2 联网** | 访问外部 API/网页 | **自动但受限**：域名白名单必须显式声明且命中预置清单；无凭据；首次调用只允许读操作 |
| **R3 高副作用** | 写入外部账号、支付、系统设置、凭据、删除类 | **必须人工**（保持现状），且默认不自动生成 |

分级由生成器在 `manifest.json` 里声明，**由验证器复核**（静态扫描实际 import/调用，声明与实现不符直接拒绝），不能只信生成器自述。

### 12.3 自动准入的三个替代机制

1. **可验证信号代替人工判断**
   - G1 静态扫描、G2 子进程导入、G3 冒烟 + 单测全部通过才挂载；
   - 挂载后进入 staging：只有创建它的脑/会话能调用，避免污染全局。
2. **预算与熔断代替事前审批**
   - 每个新能力初始预算：N 次调用 / M 秒 CPU / 单次超时；超预算自动挂起并 emit `capability_suspended`；
   - 成功率 < 80%、连续 3 次失败、或单次耗时超 2 倍声明值 → 自动降级到 `deprecated` 并回滚。
3. **事后审计代替事前点击**
   - WebUI 的"能力库"页展示：来源、生成会话、通过的闸门、调用次数/成功率、最近错误；
   - 每个能力一个"一键回滚/停用"按钮；用户是**异步复核**，不是**同步批准**。

### 12.4 保留人工确认的红线（不建议自动化）

- 联网安装第三方包 / MCP 服务（供应链不可控，必须看来源与安装命令）；
- 任何涉及凭据、支付、外部账号写入的能力；
- `git commit/push`、删除类操作、系统级配置修改；
- 合规敏感能力（刷量、群控、自动私信）——直接禁止生成，而不是靠确认。

### 12.5 渐进放开路径

| 阶段 | 自动化程度 |
|---|---|
| P0（已实现） | R0/R1/R2 全自动准入；R3 需人工确认。启动时按登记表核查，未准入的能力自动隔离 |
| P1 | 引入观察期统计，自动晋升/弃用；人工只处理被熔断的异常项 |

> 本节取代 §7.4 中"P0 由用户点启用"和 §9 中"人工启用"的表述：R0/R1/R2 免点击，只保留 R3 的确认。

---

## 13. P0 实测记录（2026-10-21 已验证）

### 13.1 落地形态

全部逻辑封装在**一个能力域** `LLM_v2/domains/capability_ops/` 内，未改动 DomainAssembly / EventBus / 三脑。

| 文件 | 职责 |
|---|---|
| `capability_ops.py` | 域类（6 工具 + 订阅 `TASK_DONE` / `DOMAIN_TOOL_CALLED`）+ 启动核查 |
| `capability_ops_impl.py` | G1 静态扫描、G2 子进程导入、G3 冒烟调用、准入、熔断、回滚、注册表 |
| `capability_ops_tools.py` | `cap_list / cap_verify / cap_admit / cap_suspend / cap_resume / cap_call` |

依赖的既有扩展点：域目录自动发现 + `build_<id>_domain()`、`assembly.rediscover()`、`stop_domain()`、`start_domain()`、事件总线。

### 13.2 实测通过的完整链路

```
用户指令 → 意志脑 code_task → 编码闭环写文件 → TASK_DONE
        → 自动准入（R0/R1/R2 免点击）→ G1 → G2 → G3 → rediscover → active
```

实测结果（会话 #8，7 轮 / 6 次工具）：

```
time_info   active   R0
G1 ✓ · G2 ✓ · G3 ✓ (get_current_time ✓)
调用返回 {"success": true, "time": "00:13:49", "date": "2026-10-21"}
```

另一条实测：`hf_hub`（R2 联网）两个工具均通过 G3，真实查询 HuggingFace 成功。

### 13.3 踩坑记录（都已修复，按重要性排序）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 编码闭环 8 轮全在侦察，从未写文件 | 提示词只说"先侦察"却没给预算 | 新增 `RECON_ROUNDS=3` + 超预算强制注入"立即 os_create_file"；`max_rounds` 8→14 |
| 2 | `run()` 在第一轮前抛异常、会话卡在 `running` | 提示词里写了 JSON 花括号，撞坏 `SYSTEM_TEMPLATE.format()` | 提示词禁用裸花括号；模板格式化单独校验 |
| 3 | AI 把 `build_<id>_domain()` 写成返回 dict | 编码循环提示词没有能力域契约 | 提示词补【新建能力域时的硬性契约】 |
| 4 | G3 报 `has no attribute get_current_time` | 工具函数只在 `_tools.py` 模块级，未绑定到域实例 | 契约补"必须能在域实例上取到（方法 / setattr）" |
| 5 | 指令被判成聊天，主播脑只输出代码块 | 关键词表缺"修正""能力域" | 动作词加 修正/改正，对象词加 能力域/插件 |
| 6 | 被拒绝的能力重启后被移进隔离区，AI 无法修复 | 启动核查对 rejected/pending 也做了 move | 改为**只停用、保留文件**；只有熔断回滚才 move |
| 7 | 超时分支不发审计事件，熔断统计看不到 | `call_tool` 的 TimeoutError 分支漏 emit | 超时也 emit `DOMAIN_TOOL_CALLED`（success=false） |
| 8 | 三道闸门重复跑，进度长时间静默 | `admit` 每次都重新验证 | 按目录指纹缓存验证结果 + 每阶段实时日志 |

### 13.4 两个必须记住的契约（否则生成的能力一定失败）

1. **工厂必须返回域实例**，不能返回 dict。
2. **工具函数必须能在域实例上直接取到**：写成类方法，或在 `__init__` 里 `setattr` 绑定。框架调度是 `getattr(domain, tool_name)`。

工具函数还必须返回含 `success` 的 dict。

### 13.5 仍需注意

- `capability_ops` 的 `start()` 若失败，`TASK_DONE` 不会订阅、自动准入静默失效 —— 启动日志里必须看到 `已订阅 TASK_DONE / DOMAIN_TOOL_CALLED`。
- G3 的 `smoke` 参数必须由作者声明；没声明就只能跳过，不能自动编参。
- R3 仍保留人工确认，不做自动化。

---

## 14. 参考来源

- Voyager：An Open-Ended Embodied Agent with LLMs — https://arxiv.org/abs/2305.16291
- Alita: Generalist Agent with Minimal Predefinition and Maximal Self-Evolution — https://arxiv.org/abs/2505.20286
- MUSE-Autoskill: Self-Evolving Agents via Skill Creation, Memory, Management, and Evaluation — https://arxiv.org/abs/2605.27366
- EvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification — https://arxiv.org/html/2604.01687v1
- A Survey of Self-Evolving Agents — https://arxiv.org/abs/2507.21046
- OpenClaw Superpowers 架构分析（2026-09）— https://dailyaiworld.com/blogs/openc-law-superpowers-self-modifying-skill-libraries-autonomous-agents
- SkillOpt（Microsoft Research）— https://mlhive.com/2026/05/microsoft-skillopt-self-evolving-ai-agents
- Anthropic Agent Skills（渐进式披露）— https://claude.com/blog/building-agents-with-skills-equipping-agents-for-specialized-work
- Claude Code 技能实践 — https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills
- MCPfinder — https://mcpfinder.dev/
- agent-discover — https://www.npmjs.com/package/agent-discover
- mcpm（MCP 安全护栏）— https://www.npmjs.com/package/@getmcpm/cli
- MCP 生产部署三缺口 — https://arxiv.org/abs/2603.13417
- 2026 人工智能产业十大关键词（有界自进化）— http://m.toutiao.com/group/7698730246673007167/
- ExpeL: LLM Agents Are Experiential Learners — https://arxiv.org/abs/2308.10144 · https://github.com/LeapLabTHU/ExpeL
- Reflexion: Language Agents with Verbal Reinforcement Learning — https://arxiv.org/abs/2303.11366 · https://github.com/noahshinn/reflexion
- Agent Workflow Memory — https://arxiv.org/abs/2409.07429 · https://github.com/zorazrw/agent-workflow-memory
- ACE: Agentic Context Engineering（ICLR 2026）— https://arxiv.org/abs/2510.04618 · https://github.com/ace-agent/ace
- ReMe：Markdown 记忆工作区 — https://reme.agentscope.io/
- Alita-G: Self-Evolving Generative Agent for Agent Generation — https://arxiv.org/html/2510.23601
