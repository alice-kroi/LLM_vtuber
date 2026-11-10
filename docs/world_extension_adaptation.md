# World 扩展化改造清单（对齐 Cortico World 扩展机制）

> 文档日期：2026-09-23
> 对照对象：Cortico `WorldDefinition` 契约 + `WorldAssembly` 装配层 + 多 World 并行机制
> 改造对象：本项目"操作脑"能力域（`LLM/operator_agent.py`、`tool/tool_node.py`、`LLM/task_manager.py`）
> 目标：把当前**单一扁平的操作脑**改造成**可热插拔、多技能域并存**的架构，实现类似可提"快速插一个游戏世界、并排玩多个"的扩展能力。

## 0. 结论摘要

当前操作脑是一个**单入口、扁平白名单**的架构：`OPERATOR_TOOL_NAMES` 列出所有操作工具，`run_operator` 用一份固定 prompt 跑 ReAct 循环，`tool_registry` 全局平铺注册所有工具。它已经做到了"工具级"复用，但**没有"模块/域"级**的插件封装：加一块新能力（比如某个游戏）就要往白名单里塞工具、改 prompt、动路由，改动会污染现有操作脑。

可提的 World 机制本质是三层：
1. **契约化封装**（每个游戏 = 一个 `WorldDefinition`，自带 `defaults()/create()/tools()/start/stop`）
2. **装配层自动挂载**（`WorldAssembly` 构造实例到 slot、检测工具名冲突、activate/deactivate 热生效）
3. **多 World 并行隔离**（工具前缀隔离 + 事件 source 路由 + 子进程隔离）

**优先级判断**：本项目不重建 TypeScript 整套框架，只需落 3 条核心（能力域契约 + 装配/挂载 + 前缀隔离）即可获得"快速插"的主体收益；P1 是调度对齐；P2 是进程级隔离等加固。

## 1. 现状 vs 可提 差距表

| 机制 | 现状（操作脑） | 可提 World | 差距 | 优先级 |
|---|---|---|---|---|
| 能力域封装 | `OPERATOR_TOOL_NAMES` 扁平白名单，[operator_agent.py](file:///e:/GitHub/LLM_vtuber/LLM/operator_agent.py#L28-L46) | 每个游戏一个 `WorldDefinition`（`id/label/defaults()/create()/tools()/start/stop`） | **缺，核心**：无"模块"概念 | **P0** |
| 装配/挂载层 | `tool_registry.register_tool` 全局平铺，[tool_node.py](file:///e:/GitHub/LLM_vtuber/tool/tool_node.py#L49-L142) | `WorldAssembly`：构造实例到 slot、`mounted` 标记、activate/deactivate/restart 热生效 | **缺，核心**：所有工具平等注册，无域归属 | **P0** |
| 工具名冲突检测 | 无——重复注册直接覆盖 name | `toolClash` 检测 + `reservedToolNames` 保留字 | 缺 | P0 |
| 前缀隔离 | 部分天然带前缀（`os_/ui_/vision_/browser_`），但无强制 | 每个 World 用 `mc_/pv_/vv_` 前缀隔离工具命名空间 | 已有雏形，缺强制/校验 | P1 |
| 启用开关 | 无——`task_manager_enabled` 只是全局 TASK 开关，[LLM_node.py](file:///e:/GitHub/LLM_vtuber/LLM/LLM_node.py#L927-L942) | `worlds.<id>.enabled` 每个域独立开关，`defaults()` 声明默认 | 缺：能力域级开关 | P1 |
| 事件 source 路由 | `operator_result` 单一回灌给主播 | 每个 World 事件带 `source`，Core 按 source 路由 | 缺：结果无域归属 | P1 |
| 子进程隔离 | 靠 `TaskManager` worker 池异步后台，[task_manager.py](file:///e:/GitHub/LLM_vtuber/LLM/task_manager.py#L92-L130)，同步回退走 `operator_worker_node` | 多游戏子进程隔离（一个多玩家子进程 + 可热拔插多 World） | 已有异步池，缺进程级隔离 | P2（本地单机可选） |
| 生命周期钩子 | 无 start/stop | World `start`/`stop`，热插拔时启停资源 | 缺 | P2（配合重连/资源释放） |
| 部署配置覆盖 | 无 | `botDir` 存本机配置/密钥，`persist()` 深合并 | 缺（环境变量已有，但无域级配置段） | P2 |

## 2. 改造清单

### P0 — 域级插件化核心（建议立即做，改动集中在 operator_agent.py + tool_node.py）

**P0-1 定义能力域契约 `SkillWorld`**
模仿 `WorldDefinition`，在 `LLM/` 下新增 `skill_world.py`，一个能力域 = 一个 `SkillWorld`：
```
class SkillWorld:
    id: str                     # 如 "desktop" / "game_mc" / "browser"
    label: str
    default_enabled: bool = True
    tools(): list[dict]         # 该域的工具 Schema 子集（从 computer_use/EXTRA 过滤）
    allowed_names() -> list[str]# 该域允许调用的工具名（对应 OPERATOR_TOOL_NAMES 分域）
    system_prompt_fragment(): str   # 补充到 operator prompt 的域说明
    start() / stop()  async     # 可选：域资源启停钩子（P2 用）
```
改造点：新增 `LLM/skill_world.py`；后续游戏/能力域都实现成这个契约。

**P0-2 装配层 `SkillWorldAssembly`**
仿 `WorldAssembly`，把全局 `tool_registry` 升级为"按域登记 + 全局唯一名"：
```
- register_world(world): 校验该域工具名不与已登记域冲突（toolClash）
- reserved names: 主播脑工具(browser_navigate 等) + Core 保留字自动加锁
- world_slots[id].enabled 由 config/环境决定
- activate(id)/deactivate(id)：只对激活域暴露工具 → 子集赋给 operator prompt
```
改造点：在 `tool_node.py` 加 `SkillWorldAssembly`，或新增 `LLM/skill_assembly.py`；`OPERATOR_TOOL_NAMES` 改为由"激活的域"聚合生成，不再手写死。

**P0-3 操作脑按域构建工具集与 prompt**
`run_operator` 不再用固定 `OPERATOR_TOOL_SCHEMA`，改为：
```
active = assembly.enabled_worlds()
tools  = concat(w.tools() for w in active)
prompt = BASE + "\n".join(w.system_prompt_fragment() for w in active)
```
改造点：[operator_agent.py](file:///e:/GitHub/LLM_vtuber/LLM/operator_agent.py#L49-L60) 的 `OPERATOR_TOOL_SCHEMA` 从静态改为由 `assembly` 计算（保留懒构建）；`OPERATOR_SYSTEM_PROMPT` 改成可拼装。

### P1 — 调度与路由对齐（推荐做）

**P1-1 每域启用开关**
`worlds.<id>.enabled` 落成 config.ini 段或环境变量（`SKILL_WORLD_DESKTOP_ENABLED=1` 风格），`assembly` 据此决定是否挂载。默认 `desktop` 域开，其余按需开。改造点：`SkillWorldAssembly` 构造时读配置。

**P1-2 结果带 source，路由按域回灌**
`operator_result` 增加 `source` 字段（该任务来自哪个域）。回收链路 `_on_task_done` → 主播播报时可按域决定措辞/是否走 Live2D。改造点：[task_manager.py](file:///e:/GitHub/LLM_vtuber/LLM/task_manager.py#L195-L222) `_dispatch` 把域 id 写进 `task.context`，`run_operator` 返回带 `source`。

**P1-3 工具名前缀强制校验**
在 `SkillWorldAssembly` 挂载时校验：域 id 强连字符 `dom_` 前缀，工具名必须以 `dom_` 开头，否则拒绝挂载（防跨域撞名，对应可提 `mc_/pv_/vv_`）。改造点：P0-2 的 toolClash 逻辑升级为前缀校验。

### P2 — 结构加固（可选，仅当要玩多个重资源游戏时）

**P2-1 域级生命周期钩子**
`SkillWorld.start()/stop()` 在 activate/deactivate 时调用（如切模型/建连接），复用 live2d 那套超时/重连拆法。

**P2-2 子进程隔离**
重资源域（如重量级游戏）跑独立子进程，父进程仅维护代理工具；本地单机一条龙场景可缓做，避免过度工程。

**P2-3 域级配置文件**
`skill_worlds.json` + 部署目录覆盖，对应可提 `botDir` + `persist()`。环境变量已够用，非必须。

## 3. 最小侵入实现要点

- **不推翻三脑**：改造限定在"操作脑"这半边（operator_agent + tool_node + task_manager 的域归属），`supervisor_route_node` / 主播脑 / LangGraph 边结构不动。
- **保持白名单语义**：本质是把"单个 `OPERATOR_TOOL_NAMES` 静态 list"变成"由多域聚合的动态 set"，接口变化最小，`_execute_operator_tool_name` 仍按名执行。
- **先立契约，不急着拆**：第一步只加 `SkillWorld` 契约 + 装配层，把现有 `os_/ui_/vision_/browser_` 包成 `desktop` 域；**不**一次拆 6 个域，避免并发状态割裂（对齐上次 basic_ops 文档的渐进策略）。
- **可观测**：挂载/激活/停用/工具冲突各打一条日志（对齐可提 `toolClash`/`activated`/`deactivated` 回执）。

## 4. 验证方式

1. 默认启动，`assembly` 只挂 `desktop` 域，`run_operator` 工具集与原白名单一致，功能回归通过。
2. **热插测试**：新增一个 `game_mc` 域（注册 2 个 `mc_` 前缀玩具工具），启动后 `activate("game_mc")` → 操作脑工具集出现 `mc_*`，prompt 出现域片段；`deactivate` 后消失。全程无需改 `run_operator` 主体。
3. **冲突测试**：注册第二个域带同名工具，装配层应拒绝挂载并给原因（toolClash）。
4. **回归**：桌面指挥（打开文件/搜索文件/浏览器搜索）、即时任务、任务取消，走 TaskManager 全链路不受影响。

## 5. 与可提机制映射表

| 可提概念 | 本项目落地 |
|---|---|
| `WorldDefinition`（id/label/defaults/create/tools/start/stop） | `LLM/skill_world.py` 的 `SkillWorld` 契约 |
| `WorldAssembly`（slots/mounted/activate/deactivate/toolClash） | `SkillWorldAssembly`（tool_node 或新 skill_assembly.py） |
| `worlds.<id>.enabled` + `worldDefaults` | config.ini / 环境变量 `SKILL_WORLD_<ID>_ENABLED` |
| 工具前缀隔离 `mc_/pv_/vv_` | 挂载时 `dom_` 前缀强制校验 |
| 事件 `source` 路由 | `operator_result.source` + 按域回灌主播 |
| 子进程隔离 | P2：重资源域独立子进程 + 代理工具 |
| `persist()` 部署覆盖 | P2：`skill_worlds.json` + 部署目录覆盖 |
| `reservedToolNames` | 主播脑工具集 + Core 保留字加锁 |