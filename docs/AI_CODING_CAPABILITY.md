# AI 自主编程能力设计（操作脑 Coding Loop）

> **目标**：让数字人**自己操作电脑写代码**——不只是"打开 VS Code"，而是"看懂需求 → 改文件 → 跑测试 → 看报错 → 再改 → 直到跑通"。
> **用户原话**：*"能否让我的 AI 自己能操作电脑进行编程"*
> **版本**：v1.0 ｜ 日期：2026-10-20 ｜ 状态：设计待确认
> **三条默认决策**（用户已批准方案，默认值见 §17，可改）

---

## 1. 一句话结论

**八成地基已经有了，缺的是"操作脑的推理循环"。** 文件读写、精确编辑、Shell、多步编排、进度上报全都在；但 [operator.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/operator.py) 里**没有 LLM**，它只是单向执行器——收到 `{tool, args}` 就执行，执行完就结束，无法"看着报错决定下一步"。

因此本设计的重心不是"补 10 个工具"，而是**给操作脑加一个有界的 ReAct 循环，并让它的完成判定基于可验证信号（测试通过），而不是模型自称"改好了"**。

---

## 2. 现状盘点

### 2.1 已有能力（可直接复用）

| 能力 | 载体 | 说明 |
|---|---|---|
| 读文件 | `os_read_file` | 5MB 上限 + `offset`/`max_bytes` 分块（对齐 Claude Code Read） |
| 写文件 | `os_create_file` | 文本文件写入 |
| **精确改代码** | `os_edit_file` | **Search-Replace 块协议（Aider 协议）**，多匹配时返回错误，避免误改 |
| 执行命令 | `os_run_command` | 默认 60s 超时、stdout/stderr 截断 50KB、线程池包装 |
| 目录/搜索 | `os_list_directory`、`os_find_file` | 注意：`os_find_file` 只按**文件名**搜，不能搜内容 |
| 目录树 | `os_list_directory` | 逐层读，可拼树 |
| 多步骤任务 | [operator.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/operator.py#L151-L187) `_execute_steps` | `steps = [{tool,args,description}]`，逐步执行 + 每步 `OPERATOR_PROGRESS`；**失败即中止** |
| 进度上报 | `OPERATOR_PROGRESS` | 已贯通"操作脑 → 意志脑监控表 → 主播脑播报" |
| function calling | [llm_adapter.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/llm_adapter.py#L62-L82) `chat(..., tools=)` | 返回 `{response, tokens_used, tool_calls, error}`；`will_engine._classify_intent_with_tools` 已在用 |
| 工具 schema | `assembly.active_tool_schemas()` | 运行期实时聚合，只含**已激活域**的工具 |
| 单步断言核验 | `os_check` | 断言式核验（验证操作结果、只报差异） |
| 结构化视觉 | `os_list_elements_json` / `os_click_id` / `os_type_by_label` | 兜底：只在必须走 IDE GUI 时使用 |

### 2.2 缺口（本设计要补的）

| 缺口 | 现状 | 影响 |
|---|---|---|
| **操作脑无 LLM** | 纯执行器，无推理 | **无法**"读报错 → 决策 → 重试"，这是能不能自主编程的分水岭 |
| **`code_action` 是占位** | [operator.py L53](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/operator.py#L53) `"code_action": "os_open_path"` | 意志脑说"想写代码"，实际只是**打开 VS Code** |
| **代码类路由是 demo 级** | [will_engine.py L559-562](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/will_engine.py#L559-L562) | "写文件"写死 `~/test.txt`、"读文件"读 `~/test.txt`（占位路径） |
| 无内容检索 | — | 只有按文件名搜；改代码必须先按内容定位（`ripgrep` 级能力） |
| 无 Git | — | **改坏了不能回滚**，这是"敢让它动手"的前提 |
| 无测试解析 | `os_run_command` 只回原始 stdout | 循环无法自动判断"是否通过"，只能靠模型自己读 pytest 输出 |
| 无工作区约束 | `os_run_command` 可跑任意 shell | 越权风险：可写系统任意路径 |
| 无编码专用模型 | 全局 `doubao-seed-1-8-251228` | 通用模型写代码失败率高，会显著拖垮循环 |
| 无会话持久化 | 任务历史仅内存 20 条 | 重启即失，无法复盘"上次为什么没写对" |

### 2.3 历史经验（来自项目记忆，必须继承）

1. **V1 曾有多轮工具循环**（`max rounds` 4/5），并对"电脑操作类指令"把轮次上限**动态提升到 10** —— 这项能力在 V2 迁移中**丢失**了，本设计要把它找回来（转成 coding loop 的 `max_rounds`）。
2. **抗幻觉守卫**：检测到"声称完成但本回合没有工具调用"→ 注入强提示 + 强制重试（max 2 次）。**编码任务必须复用这条**，且判定要更严（见 §6.4）。
3. **电脑操作类工具调用必须在异步线程池执行**，避免阻塞主直播进程。
4. **电脑操作/编程类指令一律跳过语义缓存**，否则可能用历史相似回复跳过真实操作。

---

## 3. 总体架构

### 3.1 三脑分工下的位置（关键论证）

有人会问：操作脑一旦有 LLM，是不是"越权"了？**不是**，理由是职责边界：

| 脑 | 原有职责 | 本设计后的职责 | 边界 |
|---|---|---|---|
| 意志脑 | 想说什么 / 要做什么 | **决定"要不要编程、编什么"**（`code_action` 携带具体目标与验收标准） | 出**目标**，不出代码 |
| **操作脑** | 动手执行 | **在目标内自主推理"怎么做到"**（选工具、读报错、重试）——即 ReAct | LLM **只用于执行内决策**；不产出对观众的话、不做价值观判断 |
| 主播脑 | 只说 | 把"改了什么、跑通没有、卡在哪"讲成人话 | 不参与任何工具调用与代码判断 |

> **一句话**：意志脑决定 **what**，操作脑决定 **how**，主播脑负责 **say**。
> 操作脑的 LLM 是"手上功夫"，不是"心里想法"，因此不违反三脑分工。

### 3.2 数据流

```
意志脑  decide → code_action {goal, workspace, acceptance}
   │  WILL_ENGINE_ACTION (type=execute_tool, tool=code_task)
   ▼
操作脑  Coding Loop（新增，有界 ReAct）
   ① 侦察   code_tree + code_search + os_read_file
   ② 计划   产出文件级步骤（可展示给观众）
   ③ 修改   code_apply_patch / os_create_file / os_edit_file
   ④ 验证   code_run_tests（首选）/ os_run_command
   ⑤ 失败 → 解析 stderr/测试报告 → 回到 ③（≤ max_rounds）
   ⑥ 通过 → code_git_diff 摘要（可选 commit）
   │  OPERATOR_PROGRESS（每步）+ TASK_DONE / TASK_FAILED
   ▼
意志脑  总结"做了什么、结果如何" → PROGRESS_REPORT
   ▼
主播脑  emit TTS_SPEAK（只说）
```

> 注意：**验证通过才算完成**，`TASK_DONE` 只在这个条件下发出（见 §6.4）。

### 3.3 为什么循环放操作脑（而非独立 coder 域）

- 放操作脑：符合三脑职责（执行内推理属于"动手"），复用既有 `_execute_steps`/进度/事件链，改动面小；
- 放 coder 域：域会变成"有脑子的域"，与"域=能力、脑=决策"的既有分工冲突（项目记忆：能力域无心跳、只管能力）。

**结论：循环放操作脑**；但**工具**放独立 `coder` 域（见 §4.1）。

---

## 4. 工具层

### 4.1 新建 `coder` 能力域（Actuator，前缀 `code_`）

**为什么不加进 desktop 域**：`desktop` 语义是"桌面操作"（窗口/键鼠/文件），而 `code_search`/`git_*`/`run_tests` 是**代码工程操作**，混进去会让 `desktop` 变成杂货铺。参照 `bili` → `bili_live` / `bili_video` 的既有拆分惯例，新开一域更清晰，且可独立启停（`--no-coder`）。

目录：`LLM_v2/domains/coder/`，文件沿用既有域范式：
`__init__.py`、`coder.py`（域类 + 核心逻辑）、`coder_tools.py`（schema）、`coder_engine.py`（子进程入口，JSON 行协议）。

**工具清单（P0 必做 6 个）**：

| 工具 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `code_tree` | `root`, `depth=3`, `max_entries=200` | 目录树文本 + 文件计数 | 自动忽略 `.git`/`__pycache__`/`node_modules`/`.venv`/`dist`/`build`/`*.pyc` |
| `code_search` | `pattern`(支持正则), `root`, `glob="*"`, `context_lines=2`, `max_hits=50` | 命中列表 `{file, line, text, context}` | **内容检索**（等价 ripgrep），改代码第一步 |
| `code_run_tests` | `cmd`(如 `pytest -q tests/`), `cwd`, `timeout=300` | **结构化**：`{passed, failed, errors, first_failure: {file, line, message, excerpt}}` | 解析 pytest 输出为可判定信号 |
| `code_git_status` | `cwd` | `{branch, changed:[...], untracked:[...]}` | 动手前确认工作区干净 |
| `code_git_diff` | `cwd`, `stat=False` | diff 文本 / 统计 | 给循环看"我改了什么"，也给主播脑说"改了什么" |
| `code_git_restore` | `cwd`, `paths=[]` 或 `all=False` | 回滚结果 | **改坏后的保险绳** |

**P1 追加**：

| 工具 | 说明 |
|---|---|
| `code_apply_patch` | 多文件补丁一次应用；**任一处失败则整批回滚**（写前快照） |
| `code_git_commit` | 提交（默认 `--dry-run` 需显式确认，见 §7.4） |
| `code_diff_preview` | 应用前预览 diff（人工确认用） |

**P2 可选**：`code_lint`（ruff/eslint 结构化输出）、`code_run_script`（跑任意项目脚本，仍受 §7 沙箱约束）。

### 4.2 为什么不直接用现成工具拼

现成 4 件（`os_read_file`/`os_create_file`/`os_edit_file`/`os_run_command`）**在 P0 仍然要用**（它们是通用文件/命令能力），但要补三件事：

1. `os_run_command` 只回**原始 stdout** → 循环需要**结构化判定**（`code_run_tests` 的 `passed/failed/first_failure`）；
2. `os_edit_file` 是**单文件单块**替换 → 多文件改动的原子性需要 `code_apply_patch`；
3. `os_find_file` 只搜文件名 → 需要 `code_search` 搜内容。

---

## 5. 编码循环（有界 ReAct）

### 5.1 状态机

```
IDLE
 └─(收到 code_task)→ RECON ──→ PLAN ──→ EDIT ──→ VERIFY
                      ▲                            │
                      │                     ┌──────┴──────┐
                      │                  通过            失败
                      │                    │              │
                      │                 DONE(成功)    ┌───┴────┐
                      │                              │ 可重试？ │
                      └──────────────────────────────┤  是→EDIT │
                                                      │  否→FAIL │
                                                      └──────────┘
```

- **RECON**：`code_git_status`（工作区是否干净）→ `code_tree`（项目轮廓）→ `code_search`（定位相关文件）→ `os_read_file`（读关键片段）。
- **PLAN**：让 LLM 输出**文件级**步骤（`[{file, action, intent}]`），并 emit 一条 `OPERATOR_PROGRESS`（这句能直接给观众听）。
- **EDIT**：按计划逐步应用；优先 `os_edit_file`（单点精确改），跨文件用 `code_apply_patch`。
- **VERIFY**：`code_run_tests` 优先；无测试框架则退化为 `os_run_command`（编译/语法检查）+ `os_check` 断言。
- **失败**：解析 `first_failure`，把"文件+行号+错误摘要"作为**下一轮的输入**（这是循环比一次性生成强的地方）。

### 5.2 轮次与预算

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_rounds` | **8** | 继承 V1"电脑操作类动态提升到 10"的思路；可配到 15 |
| `no_progress_rounds` | **2** | 连续两轮**同一错误**未变 → 提前中止（省 token、防死循环） |
| 单轮 LLM 超时 | 30s | 复用 `asyncio.wait_for` |
| 单步工具超时 | 域声明优先 | **用 `tool_timeouts`，不硬编码 60s**（既有硬性约束） |
| 测试超时 | 300s | 编译/测试天然慢 |
| 会话总预算 | 20 分钟 / 40 次工具调用 | 任一超限 → `TASK_FAILED` |

### 5.3 提示词结构（与意志脑 8 段装配同思路）

| # | 段 | 内容 |
|---|---|---|
| 1 | 角色 | "你是操作脑的编程执行器：目标内的 how，不产出对观众的话" |
| 2 | **目标** | 意志脑给的 `goal` + `acceptance`（验收标准） |
| 3 | **工作区** | `workspace` 根路径 + 白名单边界（§7.1） |
| 4 | **仓库地图** | `code_tree` 摘要（文件+行数+顶层符号），受预算裁剪（§6） |
| 5 | **相关片段** | `code_search` 召回的片段（top-k）+ 最近读过的文件 |
| 6 | **历史** | 本会话前几轮的 `{动作, 结果, 错误}` |
| 7 | **工具** | `assembly.active_tool_schemas()` 过滤后的 `code_*` + 允许的 `os_*` |
| 8 | **输出** | 严格 JSON：`{"thought","plan","tool","args"}` 或 `{"final": {...}}` |

### 5.4 完成判定（最关键的硬规则）

```python
done = tests_passed and no_regression      # 首选：结构化测试结果
    or (compile_ok and os_check_ok)        # 退化：编译 + 断言
# 不接受以下任何形式：
#   ✗ 模型说"已修复"
#   ✗ 模型说"应该可以了"
#   ✗ 只改了文件但没跑验证
```

- **未验证的改动一律不算完成**；若 `max_rounds` 耗尽仍有改动 → 发 `TASK_FAILED` 并附 `git_diff` 摘要 + 最后一个错误 → 由意志脑决定"要不要回滚/要不要告诉观众"。
- **抗幻觉守卫**（继承并加强）：若某轮 LLM **既没有 tool_call 又没有合法 `final`**，注入强提示并强制重试（max 2）；若它声称完成但本轮**没有任何 EDIT 或 VERIFY 类工具调用**，直接判该轮无效。

---

## 6. 仓储上下文控制

编程任务的 prompt 预算远比闲聊大，必须有分层控制：

| 层 | 内容 | 预算 | 生成方式 |
|---|---|---|---|
| L1 仓库地图 | 文件路径 + 行数 + 顶层函数/类名 | ≤ 600 tokens | 一次 `code_tree` + 轻量符号提取（正则，不引依赖） |
| L2 命中片段 | `code_search` 结果 ±2 行 | ≤ 1200 tokens | 按命中数优先级截断 |
| L3 精读文件 | 完整读入的相关文件 | ≤ 2000 tokens/文件，累计 ≤ 3000 | 只读"计划要改的"文件 |
| L4 错误上下文 | 上一轮失败的文件+行号+错误摘要 | ≤ 400 tokens | 从 `code_run_tests` 结构化结果取 |
| 合计 | — | **≤ 5200 tokens** | 超限按 L4 → L3 → L2 → L1 顺序截断（**错误上下文最后截，因为它是修正的唯一线索**） |

> 与意志脑的截断原则同构：**"能让我改对的信息"优先级最高**。

---

## 7. 安全边界（敢让它动手的前提）

### 7.1 工作区沙箱

- 每次 `code_task` 必须携带 `workspace`（**白名单根目录**），默认 `e:\GitHub\LLM_vtuber`。
- 所有 `code_*` 工具的 `root`/`cwd`/`paths` **必须落在 `workspace` 内**，越界直接拒绝（`{"success": false, "error": "越出工作区"}`）。
- `os_run_command` 的 `cwd` 同样受此约束；**命令字符串本身**再走 §7.2 黑名单。

### 7.2 危险命令黑名单（硬拦截，不可被 LLM 说服）

| 类别 | 拦截项 |
|---|---|
| 破坏性文件操作 | `rm -rf`、`del /f /s /q`（对工作区外）、`format`、`diskpart`、`shutdown`、`reg delete` |
| 危险 Git | `git push --force`、`git reset --hard`（对非本次改动）、`git clean -fdx`、`git checkout -- .` |
| 权限/系统 | `takeown`、`icacls`、`bcdedit`、修改系统目录 |
| 凭据外泄 | 任何把 `.env`/`SESSDATA`/`*_TOKEN` 内容写进文件或发出的命令 |
| 网络副作用 | `curl|bash` 类管道执行、向外部主机上传工作区内容 |

> 与项目既有安全约束一致：**凭据一律走环境变量、不硬编码**；远端推送与删除操作**强制人工确认**。

### 7.3 快照与回滚

- 每次 EDIT 前：记录 `git status` + 被改文件的**内容哈希**；
- 单次 EDIT 失败或 VERIFY 连续失败 → 自动 `code_git_restore` 到本次操作前；
- 会话级：保留 `git stash` 点，便于"整体撤销这次编程"。

### 7.4 人工确认闸门

| 操作 | 闸门 |
|---|---|
| 工作区内改文件 / 跑测试 | **自动**（这是核心能力） |
| `git commit` | 默认 `--dry-run` 出 diff，**需用户/意志脑确认** |
| `git push`、删除文件、越界路径、黑名单命令 | **强制确认**，P0 直接拒绝 |
| 新建项目 + `git init` | **P0 拒绝**（见 §17 决策点 2） |

### 7.5 与既有硬约束的衔接

- 所有工具走**异步线程池**（不阻塞直播主进程）；
- 编程指令**跳过语义缓存**；
- 工具超时用**域声明的 `tool_timeouts`**，不硬编码；
- 执行期任何异常**不得逃逸**到心跳，只降级为 `TASK_FAILED`。

---

## 8. 模型路由

`code_task` 内部调用**编码专用模型**，与闲聊/决策模型分离：

| 用途 | 模型 | 理由 |
|---|---|---|
| 编码循环（本设计） | **doubao-seed-code 系列**（具体名待确认，见 §17） | 通用模型写代码失败率高，会放大轮次与 token |
| 意志脑决策 / 主播脑说话 | 保持现状（character 系列） | 数字人实时对话优先低延迟 |

实现：`LLMAdapter` 增 `for_coding()` 工厂或 `purpose="coding"` 参数，读 `config.ini [model] coding_model` + 环境变量 `DOUBAO_CODING_MODEL`，**环境变量优先**（既有约定）。模型不可用 → 回退通用模型并记 warning（降级不阻塞）。

---

## 9. 直播可见性

**编程会比对话慢一个数量级**（编译/测试几十秒到几分钟），直播里观众看到的是长时间无反应。所以**进度播报的重要性不低于执行本身**。

### 9.1 播报策略（默认：关键节点，不逐条）

| 节点 | 事件 | 观众听到的话（主播脑生成） |
|---|---|---|
| 开始 | `TASK_STARTED` | "我打算改 xxx，先看看代码结构" |
| 计划 | `OPERATOR_PROGRESS(plan)` | "要动 3 个文件：先改解析、再加个判断、最后补测试" |
| 每轮编辑 | `OPERATOR_PROGRESS(edit)` | "第 2 处改好了，去跑一下测试" |
| 测试结果 | `OPERATOR_PROGRESS(verify)` | "跑出 2 个失败，都在同一个函数上" |
| 成功 | `TASK_DONE` | "通过了！一共改了 3 个文件" |
| 失败 | `TASK_FAILED` | "这个我卡住了，试了 5 轮还是同一个报错，先问问你" |

- **默认不逐条播报每个 tool call**（会刷屏且无信息量）；
- 进度百分比沿用既有 `OPERATOR_PROGRESS`（操作脑已有 `_emit_progress`）；
- 需要"沉浸感"时可开 `verbose_coding=True`（P3 可选）。

### 9.2 时长预期

| 任务类型 | 预估 |
|---|---|
| 读代码 + 回答"这段干嘛的" | 10~30s |
| 单文件小改 + 跑测试 | 30~90s |
| 多文件改动 + 测试迭代 | 2~5min |
| 写一个小脚本/小游戏 | 3~10min |

> 建议给意志脑的播报模板加一句"预计要一两分钟"，先给观众预期，避免干等。

---

## 10. 数据结构（沿用 `persona/state.db` 同库同 `StateDB` 模式）

```sql
-- 编程会话：一次 code_task 一行
CREATE TABLE IF NOT EXISTS coding_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    goal          TEXT NOT NULL,   -- 意志脑给的目标
    acceptance    TEXT,            -- 验收标准
    workspace     TEXT,            -- 白名单根
    status        TEXT,            -- running / done / failed / aborted
    rounds        INTEGER DEFAULT 0,
    tool_calls    INTEGER DEFAULT 0,
    model         TEXT,            -- 用了哪个编码模型
    files_changed TEXT,            -- JSON: [{file, added, removed}]
    final_error   TEXT,
    git_diff_stat TEXT             -- 收尾时的 diff 统计
);

-- 每轮动作：一轮一行（含失败的）
CREATE TABLE IF NOT EXISTS coding_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    round_no    INTEGER NOT NULL,
    phase       TEXT,          -- recon / plan / edit / verify
    tool        TEXT,
    args_digest TEXT,          -- 参数摘要（不存全文，避免膨胀）
    success     INTEGER,
    detail      TEXT,          -- 错误摘要 / 测试结果摘要
    at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_coding_steps_sess ON coding_steps(session_id, round_no);
```

**用途**：① 重启后可回放"上次为什么没写对"（对应意志脑的启动回放）；② 供意志脑把编程结果写成**经验**（状态签名-动作-结果三元组）；③ WebUI 可视化（P3）。

---

## 11. 文件清单与改造点

| 类型 | 文件 | 内容 |
|---|---|---|
| 新增 | `LLM_v2/domains/coder/{__init__.py,coder.py,coder_tools.py,coder_engine.py}` | `code_*` 工具域（§4.1） |
| 新增 | `tool/code_ops.py` | `code_*` 工具实现（search/tree/git/tests），与 `computer_use.py` 并列而非混入 |
| 新增 | `LLM_v2/brains/coding_loop.py` | 有界 ReAct 循环（状态机、预算、完成判定、抗幻觉守卫） |
| 修改 | [operator.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/operator.py#L49-L62) | `code_action` 从 `os_open_path` 改为 `code_task`；新增 `_on_code_task` 分支进入循环 |
| 修改 | [will_engine.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/brains/will_engine.py#L559-L562) | 代码类路由去掉 `~/test.txt` 占位，改为解析 `goal/workspace/acceptance` |
| 修改 | `LLM_v2/core/llm_adapter.py` | 增编码模型路由（`purpose="coding"`） |
| 修改 | `LLM_v2/core/persona_runtime.py` `StateDB` | 追加 `coding_sessions` / `coding_steps` 建表（幂等） |
| 修改 | `persona/state.db` | 新表落库（无迁移） |
| 新增（测试） | `scripts/test_coding_loop.py` | 见 §13 |
| 不改 | `desktop` 域 | 只**复用**其 `os_read_file`/`os_edit_file`/`os_run_command`，不搬家 |

**依赖**：仅标准库 + 现有依赖；`code_search` 用 Python 正则自实现（不引 ripgrep 二进制），`code_run_tests` 用正则解析 pytest 输出。**不新增第三方包。**

---

## 12. 分期实施

| 阶段 | 内容 | 交付 |
|---|---|---|
| **P0** 最小闭环 | `coder` 域 6 工具（tree/search/run_tests/git_status/git_diff/git_restore）+ `coding_loop` 状态机 + `code_task` 接入操作脑 + 工作区沙箱 + 完成判定 | **能改单个文件并跑到测试通过** |
| **P1** 多文件与回滚 | `code_apply_patch`（原子）+ `code_git_commit`（需确认）+ 连续失败自动回滚 + `coding_*` 落库 | 能做多文件改动且改坏能还原 |
| **P2** 上下文与模型 | L1-L4 仓储上下文控制 + 编码专用模型路由 + 会话回放 | 大项目里也能定位、失败率下降 |
| **P3** 可视化与体验 | WebUI 编程面板（会话/轮次/diff）+ `verbose_coding` 逐条播报 + 新项目 `git init` | 可观测、可人工介入 |

每阶段独立可回滚：P0 完成后即使 P1/P2 未做，**能力已可用**（只是单文件、无自动回滚）。

---

## 13. 验证方案

### 13.1 单元/脚本（`scripts/test_coding_loop.py`）

| 项 | 断言 |
|---|---|
| `code_tree` | 忽略 `.git`/`__pycache__`；`max_entries` 生效；空目录不抛异常 |
| `code_search` | 正则命中返回 `{file,line,text}`；`context_lines` 正确；无命中返回空列表而非报错 |
| `code_run_tests` | 给定全绿用例 → `passed>0, failed=0`；给定失败用例 → `first_failure.file/line` 正确解析 |
| `code_git_status/diff` | 干净仓库 → `changed=[]`；改一个文件后 → `changed` 含该文件且 diff 含改动行 |
| `code_git_restore` | 改后回滚 → 文件哈希与改前**逐字节一致** |
| **沙箱** | `root` 指向工作区外 → `success=false` 且错误信息含"越出工作区" |
| **黑名单** | `rm -rf /`、`git push --force`、`format` → 被拦截且**不实际执行** |
| **完成判定** | 伪造"模型说已修复但无 EDIT/VERIFY" → 判该轮无效；只 EDIT 未 VERIFY → **不算完成** |
| 抗幻觉守卫 | 某轮无 tool_call 也无合法 `final` → 触发强提示重试，最多 2 次后中止 |
| no-progress | 连续 2 轮同一错误 → 提前中止（`aborted`） |
| 预算 | 轮次/工具调用/总时长任一超限 → `failed` 且带原因 |
| 落库 | `coding_sessions` / `coding_steps` 记录完整，`coding_steps` 可按 `session_id` 回放 |
| 降级 | 编码模型不可用 → 回退通用模型且仍能完成（记 warning） |

### 13.2 端到端（真实验证，必须做）

**用本仓库自己当靶子**（低风险、有现成测试）：

1. 造一个**已知有 bug 的小任务**：在项目里的某个工具函数/文档中埋一个明确的错误；
2. 指令："改 `X` 让它满足 `Y`，并跑通对应测试"；
3. 观察链路：意志脑路由 → 操作脑 coding loop → 每轮 `OPERATOR_PROGRESS` → `TASK_DONE/FAILED` → 意志脑总结 → 主播脑播报；
4. 校验：`git diff` 只动了目标文件；`code_run_tests` 全绿；`coding_sessions` 有记录；
5. **负面用例**：给一个不可能完成的目标 → 应在 `no_progress_rounds` 后 `aborted`，**而不是假装成功**。

> 依用户习惯：跑真实端到端时可 `--no-bilibili --no-live2d --no-tts` 隔离变量，直接在 WebUI 发指令观察。

---

## 14. 风险与边界

| 风险 | 说明 | 缓解 |
|---|---|---|
| **改坏仓库** | 自主改代码天然有破坏性 | §7：沙箱 + 快照 + 自动回滚 + 危险命令黑名单 + `git commit` 需确认 |
| **死循环烧 token** | 模型反复同一个错误 | `no_progress_rounds=2` 提前中止 + 轮次/时长预算 |
| **假装完成** | 模型自称改好了 | §5.4：**完成判定只认可验证信号**，继承并加强抗幻觉守卫 |
| 上下文爆炸 | 大仓库塞爆 prompt | §6 L1-L4 分层预算 + 按错误上下文优先级截断 |
| 编码模型不可用 | 无编码模型或额度不足 | 回退通用模型 + warning（降级不阻塞） |
| 直播冷场 | 编译/测试期间长时间无输出 | §9 关键节点播报 + 时长预期 + 可选 verbose |
| 越权跑命令 | `os_run_command` 能力过强 | 工作区约束 + 黑名单 + `git push`/删除强制确认 |
| 与用户抢 IDE | 同时改同一工作区 | RECON 前查 `git status`；非干净工作区**先暂停并询问**（不自动 stash 用户改动） |
| 语义缓存误配 | 历史相似回复跳过真实操作 | 编程指令**一律跳过语义缓存**（既有硬约束） |

---

## 15. 与既有架构的边界（自查）

| 约束 | 遵守方式 |
|---|---|
| 意志脑决定 what / 操作脑做 how / 主播脑 say | §3.1 明确划分；操作脑 LLM 只做执行内决策 |
| 意志脑不调工具、不发声 | 不动 |
| 能力域零音频职责 | `coder` 域不 emit `TTS_SPEAK` |
| 域无心跳、只管能力 | `coder` 域的 git/tree/search 都是纯能力，无状态机 |
| 工具超时用域声明 | `coder.py` 声明 `tool_timeouts`，循环读取而非硬编码 |
| 异步线程池 | 全部阻塞 IO（git/搜索/测试）走线程池 |
| 凭据走环境变量 | 编码模型名/密钥走 `DOUBAO_CODING_MODEL` / 现有 `Doubao_API_KEY` |
| 失败隔离 | 循环内异常 → `TASK_FAILED`，不逃逸心跳 |

---

## 16. 可选深化（不做为默认，留档）

| 想法 | 为何先不做 |
|---|---|
| 引入 SWE-bench 类评测做质量基线 | 需要额外环境与算力；P2 后视情况 |
| 沙箱容器（Docker）隔离执行 | 现在已用"工作区白名单 + 黑名单"，够用；容器会破坏本地单机定位 |
| 让 AI 自己开 PR / 推远端 | **合规与安全风险高**，强制人工确认；默认拒绝 |
| 多 agent（planner + coder + reviewer 分角色） | 当前单循环 + 结构化验证已够；多 agent 会让直播延迟翻倍 |

---

## 17. 决策记录与待确认

### 17.1 已确认

| 决策 | 结论 |
|---|---|
| 总体方案 | **按"操作脑 Coding Loop + coder 域补工具"方案执行** |
| 交付顺序 | **先出设计文档，再编码** |
| 循环位置 | **放操作脑**（默认，理由见 §3.3），工具放独立 `coder` 域 |
| 工具实现位置 | 新建 `tool/code_ops.py`，与 `computer_use.py` 并列；**不混入 desktop 域** |
| 完成判定 | **只认可验证信号**（测试通过 / 编译+断言），不接受模型自称完成 |
| 历史经验 | 继承 V1 多轮循环 + 抗幻觉守卫 + 线程池 + 跳过语义缓存 |

### 17.2 仍待确认

1. **能力边界**：P0 只允许改**白名单工作区**内的既有项目；**新建项目 + `git init` 放 P3**（默认），还是 P0 就要？
2. **直播可见性**：默认**关键节点播报**（不逐条）；是否需要 `verbose_coding` 全程沉浸式播报？
3. **编码模型具体名**：`config.ini [model] coding_model` 用哪个？我这边查到火山方舟的编码模型需确认**带日期后缀**（既有教训：短模型名会报 `InvalidEndpointOrModel.NotFound`），需要你给一个可用名，或先回退通用模型跑 P0。
4. **工作区默认值**：默认 `e:\GitHub\LLM_vtuber`（本仓库自测），是否还要加白名单（如 `F:\` 下其他项目）？
5. **`git commit` 闸门**：是否要"意志脑自行判断可提交 + 事后告知"？默认是**必须人工确认**。

---

**版本**：v1.0 ｜ 日期：2026-10-20 ｜ 状态：设计待确认
