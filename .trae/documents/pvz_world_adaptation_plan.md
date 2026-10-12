# PvZ World 扩展本地化适配方案（纯 Python · 内存读取 + 键鼠）

> 目标：把上游 `cortico-world-pvz` 的植物大战僵尸语义世界扩展"添加扩展到本地，对我的项目进行适配"。
> 方向（用户确认）：**纯 Python**（弃 C++ 原生桥与 Node 语义引擎）、**内存读取 + 键鼠**（语义强，非视觉回退）、克隆上游仓库到 `F:\`、对**当前 Steam 版**适配。
> 载体：本项目纯 Python 的 `SubprocessSkillWorld`（[skill_world.py](file:///e:/GitHub/LLM_vtuber/LLM/skill_world.py)）——child 用 Python 写，父进程转发 `pvz_*` 代理工具，JSON 行式协议。**无需 Node、无需 VS Build Tools**。

## Context（为什么做、现状与关键阻塞）

- 本项目操作脑已升级为 `SkillWorld`/`SkillWorldAssembly`（[skill_world.py](file:///e:/GitHub/LLM_vtuber/LLM/skill_world.py)，含 P2 的 `SubprocessSkillWorld` 基类），并在 [main.py](file:///e:/GitHub/LLM_vtuber/main.py#L2942-L2947) 用 `skill_assembly.start_all()` 统一启停。新增一个游戏世界 = 新增一个 `SkillWorld` 子类挂载。上游 TS 引擎的强语义能力（`pvz_observe/pvz_do/pvz_queue/pvz_arm/pvz_stop`、太阳自动收集、卡冷却/棋盘/僵尸语义、人类语言坐标）我们希望以 Python 复刻主体。
- **目标游戏（已实测）**：`D:\steam\steamapps\common\Plants Vs Zombies\PlantsVsZombies.exe`，**FileVersion 1.2.0.1096**，exe 5,296,312 B（无版本资源节的行首为 1.2.0.1096），main.pak 45,453,180 B；SHA-256 `868F8E2B…A5CA45`/`58783264…659FC`。**≠上游 GOTY-APAC(1.2.0.1073)**。
- 环境（已实测）：env LLM = Python 3.12.12 64 位（`C:\ProgramData\anaconda3\envs\LLM\python.exe`）；**pymem 未装 / cv2 未装**，PIL+numpy 已有。需 `pip install pymem`（可选 cv2，非必需）。
- 为什么用 `pymem`：Windows 内存操作库，包装 `ReadProcessMemory`，从 Python 附加到 PvZ 进程直接读游戏对象内存（太阳数、卡牌费用与冷却百分比、每格植物、每排僵尸血量带/位置等真实内部语义），整理成 `pvz_observe` 语义局面返回给 LLM，而非靠截图猜。代价是装该依赖 + 一次性找偏移。

## 架构（纯 Python，三层）

```
主播/操作脑 ──> operator_agent ──> skill_assembly.active_tool_names()
                    │ pvz_* 代理工具（tool_registry）
                    ▼
PvzWorld(SubprocessSkillWorld)   # 父进程（env LLM Python）
    │ spawn python LLM/pvz/pvz_engine.py（JSON 行式 stdin/stdout）
    ▼
pvz_engine.py（子进程引擎）
    ├─ pvz_memory.py   # pymem/ctypes ReadProcessMemory 读游戏内存
    ├─ pvz_offsets.py  # 构建相关偏移表（gLawnApp/字段/实体步长）
    ├─ pvz_input.py    # 向游戏 HWND 发 WM_MOUSE* 消息（后台可用）+ 行列→像素
    ├─ pvz_observe / pvz_do / pvz_queue / pvz_arm / pvz_stop
    └─ 太阳自动收集 sweeper（同引擎独立 lane）
```

方案不依赖上游的注入器/植入体/`cortico/*`，只借鉴其**语义层设计**（观察差分、任务队列、触发条件、语义坐标）在 Python 重写——干净绕开版本指纹门禁与编译/Cortico 框架依赖。上游仓库克隆到 `F:\` 仅作语义与偏移的**参照**（其 `src/native/profile.h`/`tests/` 提供字段关系与校验思路）。

## 分阶段实施

### P0 — 依赖 + 项目侧 PvzWorld 接入脚手架（本阶段全部可交付、不依赖逆向）

**P0-1 依赖与环境**
- `pip install pymem`（env LLM）。`git clone https://github.com/Phantivia/cortico-world-pvz` → `F:\cortico-world-pvz`（只读参照）。
- 用 `pvz_glance`/PIL `ImageGrab.grab(bbox=game_rect)` 截帧调试（cv2 可选，不强制）。

**P0-2 新增 `LLM/pvz/` 纯 Python 包**
- `pvz_tools.py`：`PVZ_TOOLS_SCHEMA`（`pvz_observe/pvz_do/pvz_queue/pvz_arm/pvz_stop/pvz_glance` 的 JSON Schema，参数对齐上游工具协议）。
- `pvz_world.py`：`PvzWorld(SubprocessSkillWorld)`，`id="pvz"`、`label="植物大战僵尸语义世界"`、`prefix="pvz_"`、`default_enabled=False`；`allowed_names()`=上述 pvz_*；`system_prompt_fragment()`=域执行约定（只看语义局面决策、植物/排数用人类语言、太阳自动收集无需手动、一次一队列）；`launch_args()` → `["LLM/pvz/pvz_engine.py"]`（`SubprocessSkillWorld` 已用 `python_interp` 起子进程，无需改基类）。
- `pvz_engine.py`：子进程入口，`if __name__=="__main__"` 逐行读 stdin `{"method","params"}` → 分发到 observe/do/queue/arm/stop → 回 `{"result"|"error"}`。`--smoke` 时回一条打通 JSON 行供链路测试。
- `pvz_memory.py`、`pvz_offsets.py`、`pvz_input.py`、`pvz_observe 等注意逻辑`（P1 填充实现，P0 先留骨架 + `--smoke`）。
- `skill_worlds.json`：`{"pvz":{"enabled":false,"executable":"D:\\steam\\...\\PlantsVsZombies.exe","offsets_profile":"steam-1.2.0.1096","poll_hz":15,"action_timeout_ms":5000}}`（在 `LLM/` 下一份；`skill_world.py` 的 `load_config` 已支持；`world_config(id)` 取）。

**P0-3 装配与工具执行路径打通**
- [skill_world.py](file:///e:/GitHub/LLM_vtuber/LLM/skill_world.py#L340-L341) 末尾 `skill_assembly.register_world(PvzWorld())`。
- [operator_agent.py](file:///e:/GitHub/LLM_vtuber/LLM/operator_agent.py#L80-L92) `_execute_operator_tool_name` 的硬白名单 `if name not in OPERATOR_TOOL_NAMES` 改为 `skill_assembly.active_tool_names()`（desktop 行为不变，动态放行 pvz_*）。
- `PvzWorld.start()`：`super().start()` spawn 子进程 + 把各 `pvz_*` 代理工具 `tool_registry.register_tool(...)`（[tool_node.py](file:///e:/GitHub/LLM_vtuber/tool/tool_node.py#L62-L71)）；`stop()` 撤销并终止子进程。

**P0 验证（不依赖游戏正向，先用 --smoke + ping 打通链路）**：`C:\ProgramData\anaconda3\envs\LLM\python.exe` 下 `import LLM.skill_world`：装配挂 desktop + pvz 无冲突；起 `PvzWorld` → `node` 不需要，`_call_child("ping")` 回 `{"ok":true}`；`run_operator` desktop 回归不受 pvz 影响（未 enabled 时工具集与旧白名单一致）。

### P1 — 构建相关偏移表 `pvz_offsets.py`（核心工作，迭代式）

为 1.2.0.1096 确立偏移，顺序：
1. **建标注确定性**：读进程内存，按 PE 头/`gLawnApp` 访问器模式在本进程定位 `gLawnApp` 全局指针（上游 ADAPTING §3 思想，但用 Python pymem 运行时扫描，不做 C++ 字节签名编译）。
2. **试探经典公开偏移**：先试社区通用的 `module+0x329670` 系 PvZ GOTY/Steam 指针与 `gLawnApp` 地址，用观察结果校验是否命中；命中则大步复用，未命中再逐字段逆向。
3. **字段/实体表**（每项用 `ReadProcessMemory` + 类型检查，错偏移不崩、只给错误观察，参考上游 `profile.h` 组序）：
   - `app::*`：LawnApp（widgetManager/DirectDraw/board/titleSelector/seedChooser/游戏模式/场景/玩家/board 结果）。
   - `board::*`：实体数组指针、seedBank、cursor、cutScene、grid 类型、fog、冰、wave、背景、level、sun、计数、教程、铲子、进度、Cob Cannon。
   - `dataArray::*`：plant/zombie/coin/mower/gridItem 的 header/对象 size/步长（`IterateArray` 带元素上限）。
   - 实体字段：plant(类型/细胞/阶段/血量带/法向)、zombie(类型/细胞/血量带/咀嚼/速度/条带)、coin(类型=太阳/位置)、card(植物/费用/冷却秒数百分比)、mower(就绪/已触发/被压扁)。
4. 同 build 校验用**观察对照法**：`pvz_observe` 与真实画面逐项比对（title→主菜单→选档案→选种子→入局），不对就改偏移重试（P0 已备 `pvz_glance` 截帧）。

**P2 — 输入与语义任务层（P1 偏移基本稳定后）**
- `pvz_input.py`：行列→800×600 客户区像素（参考上游 cell 中心/铲子质心/种子栏/屋顶坡度像素常数实测）；点击用 **PostMessage/SendMessage 向游戏 HWND 发 `WM_LBUTTONDOWN/UP`**（后台可用、绕过桌面/IDE 抢焦点，承接项目手足教训"键鼠投递被前台占"）。可选 `SetForegroundWindow`。
- `pvz_do` 语义队列：有序 menu/选种子/种植/铲除/收集/special steps；输出人话坐标（如 `第4排第8.6列`）。因果验证：`正在执行`→`done/partial/blocked/unverified/cancelled`，回执带执行后现状。
- `pvz_arm` 触发：条件（`sun/card/cell/zombie` 谓词，`all/any/not`）成立时提交队列；`pvz_stop` 撤回任务/触发。
- 太阳自动收集 sweeper：单独 lane，读 coin 数组太阳坐标→点击；与主任务同引擎 cursor 互斥，367（不休眠)轮询。

### 装配启动与后续
- [main.py](file:///e:/GitHub/LLM_vtuber/main.py) 已 `start_all()`：确认 `PvzWorld.start()` 经装配层随应用启停（仅 enabled 时）。WebUI(8081, FastAPI 风格) 提供 pvz 域面板（start/stop/phase/pid/最近错误）——可选，user 偏好 FastAPI 界面。

## 关键文件清单
新增：`LLM/pvz/{__init__.py,pvz_world.py,pvz_engine.py,pvz_memory.py,pvz_offsets.py,pvz_input.py,pvz_tools.py}`、`LLM/skill_worlds.json`
修改：`LLM/skill_world.py`（register PvzWorld）、`LLM/operator_agent.py`（`_execute_operator_tool_name` 白名单→`skill_assembly.active_tool_names()`）、`main.py`（确认 start_all 覆盖，已具备）

复用（不重写）：`SubprocessSkillWorld` 子进程/代理工具基座 [skill_world.py](file:///e:/GitHub/LLM_vtuber/LLM/skill_world.py#L268-L327)、`ToolRegistry` [tool_node.py](file:///e:/GitHub/LLM_vtuber/tool/tool_node.py#L49-L142)、现有 `os_capture_window/PIL` 截帧；上游 F:\ 克隆仅作语义与偏移参照。

## 验证
- **P0**（无游戏、无编译，先过）：`--smoke` + `_call_child("ping")` 打通子进程链路；装配挂双域无冲突；`run_operator` desktop 回归。
- **P1/P2**（需游戏）：`pip install pymem` 后启动游戏、起 PvzWorld，`pvz_observe` 返回语义局面并对照画面一致；`pvz_do` 走 title→主菜单→选档案→选种子→入局种植/铲除/收集/暂停重启；`pvz_arm` 条件触发一次；太阳自动收集回执、物理鼠标不被占用。

## 风险与取舍
- 语义能力取决于偏移逆向质量：P1 若卡 gLawnApp/某字段，用运行时扫描 + 模型校验迭代，必要时降级该字段为图片/估算（渐进，不影响其它语义）。
- 纯内存读取被 PvZ 反作弊/校验拦截风险低（单机单进程常驻），但进程未启动/非注入态时 `pvz_observe` 返回待启动状态。
- 本轮先交付 P0（集成链路直通），P1/P2 为后续迭代；偏移逆向不确定完成时点，故不承诺。