# 任务式交互架构实施计划

## Context（背景）

当前 LLM_vtuber 已实现"三脑"架构（主播/主体/操作），但存在结构性局限：
- 操作脑 `run_operator`（LLM/operator_agent.py）是**同步阻塞**的 ReAct 循环，run_with_messages 每次 ainvoke 会阻塞等待操作完全结束才返回主播回复。
- 无任务队列、无优先级、无并行、无进度上报。
- 用户希望升级为**任务式交互**：主播提交任务 → 主体派发给操作 → 操作后台完成并实时回报进展 → 主播/用户通过 WebSocket 实时得知；支持任务优先级、即时并行任务（如"查看桌面"）。

改造目标（不影响纯聊天路径与 Live2D/TTS 串行锁）：
1. 操作类任务改为**后台异步执行**（主播先回"正在处理"，TaskManager 后台跑）。
2. 引入**优先级**（high/normal/low）与**即时并行**任务。
3. 操作过程**实时广播进度**到 WebSocket，并写回 TaskStore 供主播读取。
4. 操作完成后主播获得完成信息并向观众播报。

## 方案总览

新增中央 `TaskManager`（操作/即时任务专用），与现有 `_message_queue`（聊天专用，保持串行）**职责分离、共存**。操作任务移出 LangGraph 同步主链，改为后台 worker 池执行。

## 实施步骤

### 1. 新建 `LLM/task_manager.py`（核心）
- **Task 模型**：dataclass，字段 `task_id, type(operator|immediate|chat), priority(HIGH/NORMAL/LOW IntEnum), status(PENDING/RUNNING/DONE/FAILED/CANCELLED), progress(0-1), message, result, is_immediate, submitted_by, created_at/started_at/finished_at, cancel_event, state_snapshot`。
- **TaskManager** 单例 `task_manager`：
  - `asyncio.PriorityQueue` 存 `(priority, seq, task)`（dataclass 不可比较，用三元组保证 FIFO）。
  - `_tasks: dict` 作为 TaskStore（存活+最近完成结果）。
  - `submit(task)`：`is_immediate` → `loop.create_task` 并行派发（最多3并发）；否则入 PriorityQueue。
  - `_worker_loop()`：2 个 worker 池，pop→`_dispatch`→写结果。
  - `_on_progress(task_id, progress, message)`：`asyncio.create_task(ws_manager.broadcast("task_update",...))` + 写回 TaskStore，**不阻塞 ReAct 循环**。
  - `cancel(task_id)`、`get(task_id)`。
  - 状态机 `PENDING→RUNNING→DONE/FAILED`，协作式取消（每轮工具间查 `cancel_event`）。

### 2. `LLM/operator_agent.py` — run_operator 支持进度/取消
- `run_operator(question, tools=None, max_rounds=10, *, progress_cb=None, cancel_event=None, task_id="")`。
- 每轮 `asyncio.gather`（原 182 行）后调用 `progress_cb(rounds/max_rounds, f"正在{tool}...")`；循环头检查 `cancel_event.is_set()` 提前返回。
- 默认参数向后兼容（无 TaskManager 时图形保持原同步路径）。

### 3. `LLM/LLM_node.py` — supervisor 改为提交任务
- `supervisor_route_node`：检测到 `op_request` 时**不再同步 await operator_worker**，改为构造 `Task(type="operator", priority, is_immediate=...，state_snapshot=dict(state))` 提交给 `task_manager`；写回 `state["task_id"]`、`operation_status="accepted"`。路由恒转向 `streamer_worker`（主播先回复"正在处理"）。
- `_COMPUTER_ROUTE_KEYWORDS`/`_find_drive_mention` 现有路由判定保留。

### 4. `main.py` — graph 改道 + 主播读取任务状态
- `_route_after_supervisor`（856 行）：supervisor 后恒走 `streamer_worker`（操作已后台）。
- `_llm_process_node`（1256 行）：从 state 取 `task_id` → 查 TaskStore：
  - 任务 `DONE` → 把 `result.summary/result` 纳入 `core_info_parts`（复用 1326 中枢注入块）。
  - 任务 `RUNNING` → 注入"任务受理中"无关信息，主播回复"正在处理"。
- `on_task_done` 回调：TaskManager 任务完成时，向 `_message_queue` 注入一条"播报结果"消息（复用串行队列安全播报，不碰 Live2D/TTS 锁）。

### 5. `webui/app.py` — 加进度广播辅助
- 新增 `broadcast_task(task_id, status, progress, phase, message, priority)`，事件格式：
```json
{"type":"task","event":"task_update","timestamp":...,
 "data":{"task_id":"op_x","status":"running","progress":0.5,
         "phase":"tool_os_list_windows","message":"正在读取桌面窗口...",
         "priority":"high","type":"operator"}}
```
- 完成态 `event:"task_done"` 带 `result`；失败 `event:"task_failed"` 带 `error`。

### 6. 回退开关
- 环境变量 `TASK_MANAGER_ENABLED` 关闭时，supervisor 落回原同步 `operator_worker` 路径，图结构不变（零风险回滚）。

## 关键文件
- `e:\GitHub\LLM_vtuber\LLM\task_manager.py`（新建）
- `e:\GitHub\LLM_vtuber\LLM\operator_agent.py`
- `e:\GitHub\LLM_vtuber\LLM\LLM_node.py`
- `e:\GitHub\LLM_vtuber\main.py`
- `e:\GitHub\LLM_vtuber\webui\app.py`

## 复用的现有能力
- `ws_manager.broadcast(event,data,msg_type)`（webui/app.py:146）— 进度广播。
- `ToolRegistry.execute_tool`（tool/tool_node.py:85）— 操作工具执行。
- `_message_queue`/`_queue_worker`（main.py:278/333）— 聊天串行流与任务播报。
- 中枢信息注入块（main.py:1326-1336）— 主播读取操作结果。

## 验证方式
1. `python -m py_compile main.py LLM/task_manager.py LLM/operator_agent.py LLM/LLM_node.py webui/app.py`。
2. `python main.py` 启动后，通过 `POST /api/send` 注入操作指令（如"帮我打开E盘"），观察：
   - 主播**立即**回复"正在处理"（不阻塞）。
   - WebSocket 收到 `task_update` 进度事件（连接一条 websocket 客户端监听）。
   - 操作完成后，主播收到"播报结果"消息并向观众广播文件夹列表。
3. `POST /api/send` 注入纯聊天指令，验证仍走原串行队列、无任务系统副作用、Live2D/TTS 正常。
4. 同时提交多个操作 + 一个"查看桌面"即时任务，验证高优先级/即时并行调度。
5. 设 `TASK_MANAGER_ENABLED=0` 重启，验证回退到原同步路径。