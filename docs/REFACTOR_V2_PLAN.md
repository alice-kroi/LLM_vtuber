# LLM_vtuber V2 改造计划 — EDA + 单角色三脑架构

> **决策**：抛弃 LangGraph，采用**事件驱动架构（EDA）+ asyncio**。
> **范式**：**单角色 + 三脑**——1 个数字人（1 份 Persona + 1 个 State）由 3 个独立脑协程组成。
>
> **版本**：v1.0  |  日期：2026-10-14  |  状态：骨架阶段

---

## 一、核心设计理念

### 1.1 单角色三脑（不是多角色）

```
┌──────────────────────────────────────────────────────────────┐
│                  小鲤（1 个数字人）                            │
│                                                              │
│   ┌────────────────────────────────────────────────┐         │
│   │   共享：Persona + State                         │         │
│   │   persona/profile.yaml（人格档案）             │         │
│   │   persona/state.db（动态状态）                 │         │
│   └────────────────────────────────────────────────┘         │
│                          ↕ 读写                                │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│   │  Streamer    │  │  Operator    │  │ Will Engine  │       │
│   │  主播脑      │  │  操作脑      │  │ 意志脑       │       │
│   │              │  │              │  │              │       │
│   │ · 对外沟通   │  │ · 真实操作   │  │ · 自主决策   │       │
│   │ · TTS/Live2D │  │ · 工具调用   │  │ · 空闲触发   │       │
│   │ · 弹幕回复   │  │ · 任务执行   │  │ · 想干啥事   │       │
│   └──────────────┘  └──────────────┘  └──────────────┘       │
│         ↕                   ↕                  ↕             │
│         ╰─────── EventBus（事件总线）──────╯                  │
└──────────────────────────────────────────────────────────────┘
```

**关键设计**：
- ✅ 3 个脑**共享同一个 Persona + State**（人格一致、状态同步）
- ✅ 3 个脑**互不阻塞**（asyncio 协程独立运行）
- ✅ 3 个脑通过**事件总线**通信（解耦）
- ✅ 每个脑有独立的 system prompt（专注自己的职责）

### 1.2 为什么抛弃 LangGraph

| LangGraph 限制 | V2 解法 |
|----------------|---------|
| 串行 DAG | asyncio 协程池（并行） |
| 无持续后台 | while True 循环 |
| 无 interrupt | asyncio.Event / 事件触发 |
| 无 sub-agent | Brain 协程独立 |
| 状态不共享 | 全局单例 PersonaRuntime |

---

## 二、目录结构

```
LLM_vtuber/
├── LLM_v2/                              ← 新建（V2 核心）
│   ├── core/
│   │   ├── event_bus.py                # EDA 核心
│   │   ├── persona_runtime.py          # 单例 Persona + State
│   │   ├── brain_base.py               # 三脑基类
│   │   └── event_logger.py             # 事件日志（可观测）
│   ├── brains/
│   │   ├── streamer.py                 # 主播脑
│   │   ├── operator.py                 # 操作脑
│   │   └── will_engine.py              # 意志脑
│   ├── main.py                         # V2 启动入口
│   └── events.py                       # 事件类型常量
├── persona/                             # 已创建（Phase 1）
│   ├── profile.yaml
│   ├── persona.py
│   ├── state.py
│   └── ...
├── tool/                                # 保留（工具层）
│   ├── computer_use.py
│   ├── tool_node.py
│   └── ...
├── LLM/                                 # 保留（兼容旧接口）
│   ├── skill_world.py                  # 域机制保留
│   ├── semantic_cache.py
│   └── ...
└── docs/
    ├── ARCHITECTURE.md                  # ← 需更新到 V2
    ├── REFACTOR_V2_PLAN.md              # 本文档
    └── ...
```

---

## 三、改造阶段

### Phase A：骨架（1-2 天）✅ 当前
- [x] 创建 `LLM_v2/` 目录
- [ ] 实现 `core/event_bus.py`
- [ ] 实现 `core/persona_runtime.py`
- [ ] 实现 `core/brain_base.py`
- [ ] 实现 `brains/will_engine.py`（最简单）
- [ ] 实现 `main.py` 启动 3 个 brain
- [ ] 最小 demo：3 个 brain 互相发事件

### Phase B：迁移现有逻辑（3-5 天）
- [ ] 迁移 `LLM/LLM_node.py` → `brains/streamer.py`
  - 主对话流程 → 事件监听器
  - IntentClassifier → Lorebook 触发器
- [ ] 迁移 `LLM/operator_agent.py` → `brains/operator.py`
  - 工具调用 → on "do_task" 监听器
- [ ] 保留 `LLM/skill_world.py`（域机制）
- [ ] 保留 `LLM/semantic_cache.py`（记忆）

### Phase C：直播侧集成（2-3 天）
- [ ] 现有 `bilibili_listener.py` 改造为 event 发射器
- [ ] 现有 `tts_node.py` 改造为 event 监听器
- [ ] 现有 `live2d_main.py` 改造为 event 监听器
- [ ] VTube Studio 保留 WebSocket

### Phase D：删除 LangGraph（1 天）
- [ ] 从 `main.py` 移除 LangGraph 引用
- [ ] 保留旧文件作为备份，但不再使用
- [ ] 更新 README

### Phase E：Will Engine 增强（1-2 周）
- [ ] 实现真正的"AI 想干啥"决策
- [ ] 集成 Qwen2.5-7B 本地决策模型
- [ ] 任务队列持久化（sqlite）
- [ ] 进度可视化（WebUI）

---

## 四、EventBus 设计

### 4.1 核心 API

```python
class EventBus:
    def on(self, event_type: str, listener: Callable): ...
    async def emit(self, event_type: str, payload: Any = None): ...
    async def start(self): ...  # 启动 dispatcher
    async def stop(self): ...   # 停止
```

### 4.2 事件类型（v1）

```python
EVENTS = {
    # 输入
    "bilibili_msg": "弹幕消息",
    "gift_received": "收到礼物",
    "user_command": "主播指令",
    
    # 内部
    "idle_tick": "空闲心跳",
    "task_started": "任务开始",
    "task_done": "任务完成",
    "task_failed": "任务失败",
    "needs_clarification": "需要确认",
    
    # 输出
    "tts_speak": "TTS 说话",
    "live2d_expression": "Live2D 表情",
    "bilibili_reply": "回复弹幕",
    
    # 自主
    "will_engine_decided": "意志脑决策",
    "diary_written": "日记已写",
    "reflection_done": "反思完成",
    
    # 系统
    "error": "错误发生",
    "shutdown": "系统关闭",
}
```

### 4.3 关键特性
- ✅ **异步**：所有事件通过 asyncio.Queue
- ✅ **解耦**：listener 是 async 函数
- ✅ **错误隔离**：单 listener 失败不影响其他
- ✅ **可观测**：所有事件落日志（event_logger）
- ✅ **优先级**：可选 priority 字段

---

## 五、Brain 基类设计

```python
class Brain:
    """三脑共享基类"""
    
    def __init__(self, name: str, runtime: PersonaRuntime):
        self.name = name
        self.runtime = runtime
        self.bus = runtime.bus
        self.system_prompt = ""  # 子类填
    
    async def setup(self):
        """子类重写：注册监听器"""
        raise NotImplementedError
    
    async def run(self):
        """每个 brain 是独立协程"""
        await self.setup()
        logger.info(f"[{self.name}] brain started")
        while True:
            await self.heartbeat()
            await asyncio.sleep(self.heartbeat_interval)
    
    async def heartbeat(self):
        """子类重写：定期任务"""
        pass
    
    async def emit(self, event_type: str, payload: Any = None):
        await self.bus.emit(event_type, payload)
    
    def apply_state_event(self, event_type: str, **kwargs):
        """统一的状态变更入口"""
        self.runtime.state.apply_event(make_event(event_type, **kwargs))
        self.runtime.save_state()
```

---

## 六、PersonaRuntime 单例

```python
class PersonaRuntime:
    """Persona + State 全局单例"""
    
    def __init__(self, profile_path: str = "persona/profile.yaml",
                 state_db_path: str = "persona/state.db"):
        self.persona = PersonaProfile.load(profile_path)
        self.state_db = PersonaStateDB(state_db_path)
        self.state = self.state_db.load()
        self.bus = EventBus()  # 共享事件总线
    
    def save_state(self):
        self.state_db.save(self.state)
```

---

## 七、3 个 Brain 的具体职责

### 7.1 Streamer Brain（主播脑）

```python
class StreamerBrain(Brain):
    """对外沟通：聊天 / 弹幕 / TTS / Live2D"""
    
    async def setup(self):
        self.bus.on("bilibili_msg", self.on_bilibili_msg)
        self.bus.on("operator_done", self.on_task_done)
        self.bus.on("will_engine_action", self.execute_will_action)
        self.bus.on("needs_clarification", self.ask_clarification)
    
    async def on_bilibili_msg(self, msg):
        # 1. 拼 prompt（含 Persona + State）
        prompt = self.build_prompt(msg)
        # 2. 调 LLM
        response = await self.llm_chat(prompt)
        # 3. TTS
        await self.bus.emit("tts_speak", {"text": response})
        # 4. 弹幕回复
        await self.bus.emit("bilibili_reply", {"text": response})
        # 5. 更新状态
        self.apply_state_event("chat_replied")
```

### 7.2 Operator Brain（操作脑）

```python
class OperatorBrain(Brain):
    """真实 OS 操作：工具调用 / 任务执行"""
    
    async def setup(self):
        self.bus.on("do_task", self.execute_task)
        self.bus.on("operator_action", self.execute_action)
    
    async def execute_task(self, task):
        result = await self.run_tool(task["tool"], task["args"])
        await self.bus.emit("operator_done", {"task": task, "result": result})
        if result.get("success"):
            self.apply_state_event("task_done")
        else:
            self.apply_state_event("task_failed")
```

### 7.3 Will Engine Brain（意志脑）

```python
class WillEngineBrain(Brain):
    """自主决策：空闲触发 / 想干啥事"""
    
    async def setup(self):
        self.bus.on("idle_tick", self.on_idle)
        self.bus.on("cron_20:00", self.write_diary)
        self.bus.on("task_count_50", self.reflect)
    
    async def heartbeat(self):
        await asyncio.sleep(30)  # 每 30 秒
        await self.bus.emit("idle_tick", {})
    
    async def on_idle(self, _):
        if self.runtime.state.should_force_rest():
            await self.bus.emit("rest_requested", {})
            return
        if self.runtime.state.wants_exploration():
            action = await self.decide_what_to_do()
            await self.bus.emit("will_engine_action", action)
```

---

## 八、与现有架构的兼容性

### 8.1 保留的部分

| 组件 | 处理 |
|------|------|
| `tool/computer_use.py` | ✅ 完全保留 |
| `LLM/skill_world.py` | ✅ 完全保留（域机制） |
| `LLM/semantic_cache.py` | ✅ 完全保留 |
| Bilibili 监听器 | ⚠️ 改造为事件发射器 |
| TTS / Live2D | ⚠️ 改造为事件监听器 |
| Milvus / MinIO | ✅ 完全保留 |

### 8.2 替换的部分

| 组件 | 替换为 |
|------|--------|
| `LLM/LLM_node.py` | `LLM_v2/brains/streamer.py` |
| `LLM/operator_agent.py` | `LLM_v2/brains/operator.py` |
| `LLM/task_manager.py` | `LLM_v2/brains/will_engine.py` |
| LangGraph 主流程 | `LLM_v2/main.py` 启动 3 brain |

---

## 九、验证清单

### Phase A 骨架完成验证

- [ ] `EventBus` 单测：emit → listener 收到
- [ ] `PersonaRuntime` 单测：加载 + 保存
- [ ] `Brain` 基类：3 个 brain 同时 run
- [ ] 集成测试：brain A emit → brain B 收到

### 完整 V2 完成验证

- [ ] 主对话流程迁移完成，删掉 LangGraph
- [ ] 工具调用迁移完成，OperatorBrain 跑通
- [ ] 自主决策跑通：Will Engine 触发 → Operator 执行 → Streamer 报告
- [ ] TTS / Live2D / Bilibili 事件化
- [ ] 端到端 demo：观众说"帮我写 CNN" → 主播脑 + 操作脑 + 意志脑协作

---

## 十、风险与缓解

| 风险 | 缓解 |
|------|------|
| 现有 LangGraph 调用方太多 | 渐进迁移，先骨架后迁移 |
| 事件协议不稳定 | 严格定义 EVENTS 常量，文档化 |
| 多个 brain 状态不同步 | PersonaRuntime 单例 + save_state 集中管理 |
| 错误传播 | EventBus 安全 emit，单 listener 失败不影响 |
| 调试不直观 | event_logger.py 记录所有事件 |

---

**下一步**：立即开始 Phase A 骨架实施，先写 4 个核心文件。
