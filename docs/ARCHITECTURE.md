# LLM_vtuber 架构权威文档（Authoritative Architecture Document）

> **目的**：本文档是 LLM_vtuber 项目的**架构单一参考源**（Single Source of Truth）。
> **版本**：v5.7 — **第 4 批迁移完成（删除 ReflectionBrain，3 脑架构）**
> **日期**：2026-10-14  
> **状态**：第 4 批迁移完成 ✅  
> **维护者**：项目主理人

---

## 0. 版本演进

| 版本 | 日期 | 架构 | 主要变更 |
|------|------|------|----------|
| v1.0 | 2026-08-29 | LangGraph 单流程 | 初版 |
| v2.0 | 2026-10-14 | LangGraph + Persona + State | 新增 Persona/State 设计章节 |
| v3.0 | 2026-10-14 | **EDA + 单角色三脑** | 抛弃 LangGraph，启用事件驱动架构；新增 LLM_v2/ 目录 |
| v4.0 | 2026-10-14 | **4 脑 + 完整 IO** | **新增 ReflectionBrain；集成 WebUI / RAG / context_controller / bilibili_state；LangGraph 引用清理** |
| v5.0 | 2026-10-14 | **V2 原生能力域** | **抛弃 V1 SkillWorld 桥接，9 个能力域（pvz/launcher/bili/browser/desktop_agent/desktop_pet/ui_perception/desktop + audio）原生重写到 LLM_v2/domains/；LLM/skill_world.py + tool/tool_node.py + V1 main/webui/scripts 全部移除；DomainAssembly.call_tool() 成为唯一调度入口** |
| v5.6 | 2026-10-14 | **三型 Capability** | **H-2 抽象：CapabilityType 枚举 + Receiver / Transceiver / Actuator 三子类 + event 路由表 + USER_MESSAGE / PERCEPTION_UPDATED 通用事件 + WebUI Capability + REST / WS 双向通道 + StreamerBrain / OperatorBrain 适配** |
| **v5.7** | **2026-10-14** | **3 脑架构** | **删除 ReflectionBrain（其"洞察"产出无消费方，违反单一职责），回归干净的 3 脑：WillEngine（决策）+ Streamer（沟通）+ Operator（动作），对应 思维/嘴/手** |

---

## 1. 系统全景

### 1.1 一句话定位
**LLM_vtuber 是一个"AI 数字员工 + 数字人主播 + 数字生命体"三合一的自主系统。**

### 1.2 V3 核心架构图（EDA + 单角色三脑）

```
┌─────────────────────────────────────────────────────────────────────┐
│                       输入侧（外部 → 系统）                          │
│   Bilibili 弹幕 / 礼物 / SC                                           │
│   WebUI 控制台（8081）                                                │
│   主播指令（手动触发）                                                 │
└─────────────────────────────────┬─────────────────────────────────────┘
                                  ↓ 事件 emit
┌─────────────────────────────────────────────────────────────────────┐
│              EventBus（事件总线，EDA 核心，asyncio）                  │
│                                                                      │
│   24 种事件类型（[events.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/events.py)）│
│   - 输入：BILIBILI_MSG / GIFT_RECEIVED / USER_COMMAND                │
│   - 内部：IDLE_TICK / TASK_STARTED / TASK_DONE / TASK_FAILED        │
│   - 输出：TTS_SPEAK / LIVE2D_EXPRESSION / BILIBILI_REPLY            │
│   - 自主：WILL_ENGINE_DECIDED / DIARY_WRITTEN / REFLECTION_DONE     │
│   - 系统：ERROR / SHUTDOWN / STATE_UPDATED                          │
└─────────────────────────────────┬─────────────────────────────────────┘
                                  ↓ on(listen)
┌─────────────────────────────────────────────────────────────────────┐
│         PersonaRuntime（单例，1 个数字人 + 3 个脑共享）              │
│                                                                      │
│   - persona: PersonaProfile（[profile.yaml](file:///e:/GitHub/LLM_vtuber/persona/profile.yaml)） │
│   - state: PersonaState（PAD + 资源条，sqlite 持久化）                │
│   - bus: EventBus（三脑通信总线）                                     │
│   - apply_event() 集中入口（任何脑改 state 都走这里）                  │
└─────────────────────────────────┬─────────────────────────────────────┘
                                  ↓ 共享
┌─────────────────────────────────────────────────────────────────────┐
│              3 个 Brain（独立协程，永不停歇）                          │
│                                                                      │
│   ┌────────────────┐ ┌────────────────┐ ┌────────────────┐         │
│   │ WillEngine     │ │   Streamer     │ │   Operator     │         │
│   │ 意志脑          │ │   主播脑        │ │   操作脑        │         │
│   │ 心跳 30s        │ │   心跳 2s       │ │   心跳 3s       │         │
│   │                │ │                │ │                │         │
│   │ - 自主决策      │ │ - 对外沟通      │ │ - 真实 OS 操作  │         │
│   │ - 空闲触发      │ │ - LLM 调用      │ │ - 工具调用      │         │
│   │ - 状态监测      │ │ - TTS / Live2D │ │ - 任务执行      │         │
│   │                │ │ - RAG 检索      │ │                │         │
│   └────────────────┘ └────────────────┘ └────────────────┘         │
│         ↓                  ↓                  ↓                      │
│      emit              emit              emit                         │
│         ╰──────────────────┴──────────────────╯                      │
│                       EventBus                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  ↓ 输出事件
┌─────────────────────────────────────────────────────────────────────┐
│                       输出侧（系统 → 外部）                          │
│   TTS（GPT-SoVITS）──→ VTube Studio ──→ 直播间                       │
│   Live2D 表情动作 ────────────────────→ 直播间                       │
│   弹幕回复 ──────────────────────────→ Bilibili                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│              横向：能力域（V2 原生，LLM_v2/domains/）                 │
│                                                                      │
│   8 个能力域（V1 SkillWorld 全部迁移完成）：                          │
│     1. desktop（31 工具，✅ V2 内建域）                              │
│     2. launcher（MuMu/ALAS/MAA 启动，✅ V2 子进程域）                │
│     3. pvz（植物大战僵尸语义世界，✅ V2 子进程域）                   │
│     4. browser（受控 Edge，✅ V2 子进程域）                          │
│     5. bili（B 站监听，✅ V2 子进程域）                              │
│     6. desktop_agent（Win32 + UIA，✅ V2 子进程域）                  │
│     7. desktop_pet（桌宠，✅ V2 子进程域）                            │
│     8. ui_perception（UIA + OCR + 视觉，✅ V2 子进程域）             │
│                                                                      │
│   V2 原生基类：LLM_v2.core.capability_domain.CapabilityDomain / SubprocessCapabilityDomain │
│   V2 装配层：LLM_v2.core.domain_assembly.DomainAssembly              │
│   唯一调度入口：DomainAssembly.call_tool(name, args, timeout)        │
│   自动发现：扫描 LLM_v2/domains/<id>/<id>.py 调 build_<id>_domain()  │
│   配置：LLM/skill_worlds.json（V1 配置兼容）或 config/domains.json    │
│                                                                      │
│   集成方式：OperatorBrain 通过 runtime.assembly.call_tool 调度全部工具 │
│   域状态变化 emit DOMAIN_* 事件（WebUI EventPusher 可订阅）           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│              横向：记忆层（V2 Phase 2 升级）                          │
│                                                                      │
│   现有：                                                            │
│     - Milvus 向量长期记忆                                           │
│     - semantic_cache（仅相似度）                                    │
│                                                                      │
│   Phase 2 升级：                                                    │
│     - 三因子评分（Generative Agents）                                │
│     - 分层（MemGPT：core/working/mid/long）                         │
│     - Reflection 机制                                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. V2 目录结构

```
LLM_vtuber/
├── LLM_v2/                              ← V3 新建（EDA + 三脑）
│   ├── events.py                       # 24 种事件常量
│   ├── core/
│   │   ├── event_bus.py                # EventBus（EDA 核心）
│   │   ├── persona_runtime.py          # PersonaRuntime 单例 + StateDB
│   │   ├── brain_base.py               # Brain 抽象基类
│   │   └── llm_adapter.py              # LLM 适配器（接豆包）
│   ├── brains/
│   │   ├── streamer.py                 # 主播脑（v2：接入真实 LLM）
│   │   ├── operator.py                 # 操作脑（v1 占位，Phase B 接 tool/computer_use.py）
│   │   └── will_engine.py              # 意志脑（v1 规则版，Phase 3 接本地 LLM）
│   └── main.py                         # V2 启动入口
├── persona/                             # Phase 1 已完成
│   ├── profile.yaml
│   ├── persona.py
│   ├── state.py
│   └── ...
├── tool/                                # 保留（工具层）
│   ├── computer_use.py
│   ├── tool_node.py
│   └── ...
├── LLM/                                 # 保留（兼容旧接口）
│   ├── skill_world.py                  # 域机制
│   ├── semantic_cache.py               # 记忆
│   ├── chat_model.py                   # 豆包调用（被 llm_adapter 复用）
│   ├── LLM_node.py                     # 旧 LangGraph 节点（待迁移）
│   ├── operator_agent.py               # 旧操作脑（待迁移）
│   └── ...
└── docs/
    ├── ARCHITECTURE.md                  # ← 本文档
    ├── AUTONOMOUS_AI_VISION.md         # 终极愿景 PRD
    ├── PERSONA_STATE_ARCHITECTURE.md   # Persona + State 详细设计
    ├── REFACTOR_V2_PLAN.md             # V2 改造计划
    └── ...
```

---

## 3. 三大核心设计

### 3.1 单角色四脑（Single Character, Four Brains）

**关键原则**：1 个数字人，1 份 Persona，1 个 State，但有 4 个独立的脑协程。

| 脑 | 职责 | 心跳 | 输出事件 |
|----|------|------|----------|
| **WillEngine** | 自主决策（想干啥）| 30s | WILL_ENGINE_ACTION |
| **Streamer** | 对外沟通（聊天/弹幕/TTS）| 2s | TTS_SPEAK, BILIBILI_REPLY |
| **Operator** | 真实操作（OS 工具）| 3s | OPERATOR_DONE |
| **Reflection** | 周期反思（经验沉淀）| 10s | REFLECTION_DONE |

**共享机制**：
- 共享 `PersonaRuntime` 单例
- 任一脑改 state → 走 `runtime.apply_event()` → 自动持久化 + 通知
- 三脑互不阻塞（asyncio 独立协程）

### 3.2 EDA（事件驱动架构）

```python
# 监听
bus.on(EVENTS.BILIBILI_MSG, streamer._on_bilibili_msg)

# 发射
await bus.emit(EVENTS.TTS_SPEAK, {"text": response})

# 异步分发
asyncio.create_task(safe_invoke(listener, payload))  # 失败不传染
```

**核心特性**：
- ✅ 完全解耦（无主流程）
- ✅ 异步（asyncio.Queue）
- ✅ 错误隔离（单 listener 失败不影响其他）
- ✅ 可观测（[event_bus.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/event_bus.py) 内置统计）

### 3.3 抛弃 LangGraph 的理由

| LangGraph 限制 | V3 解法 |
|----------------|---------|
| 串行 DAG | asyncio 协程池（并行） |
| 无持续后台 | while True 循环（永不结束） |
| 无 interrupt | asyncio.Event |
| 无 sub-agent | Brain 协程独立 |
| 状态不共享 | PersonaRuntime 单例 |
| 调试复杂 | EventBus 统计 + 日志 |

---

## 4. V2 实施阶段

### Phase A：骨架（已完成 ✅）
- [x] 创建 LLM_v2/ 目录
- [x] 实现 EventBus / PersonaRuntime / Brain 基类
- [x] 实现 WillEngine / Streamer / Operator 三个 Brain（v1 占位）
- [x] 实现 main.py 启动入口
- [x] 最小骨架验证（demo_v2_skeleton.py）

### Phase B：迁移现有逻辑（进行中）
- [x] **接入真实 LLM**（[llm_adapter.py](file:///e:/GitHub/LLM_vtuber/LLM_v2/core/llm_adapter.py) 接豆包）✅
- [x] **替换 streamer 占位**（已接 LLMAdapter）✅
- [ ] 迁移 LLM_node.py → streamer.py 详细逻辑
- [ ] 迁移 operator_agent.py → operator.py（接入 31 个工具）
- [ ] 迁移 IntentClassifier → lorebook 触发器

### Phase C：直播侧集成（待开始）
- [ ] Bilibili 监听器改造为事件发射器
- [ ] TTS / Live2D 改造为事件监听器
- [ ] VTube Studio 保留 WebSocket

### Phase D：删除 LangGraph（待开始）
- [ ] 从 main.py 移除 LangGraph 引用
- [ ] 保留旧文件作为备份
- [ ] 清理 README

### Phase E：Will Engine 增强（待开始）
- [ ] 实现真正的"AI 想干啥"决策（接 Qwen2.5-7B 本地）
- [ ] 任务队列持久化（sqlite）
- [ ] 进度可视化（WebUI）

---

## 5. V2 验证结果

### 骨架验证（[demo_v2_skeleton.py](file:///e:/GitHub/LLM_vtuber/scripts/demo_v2_skeleton.py)）

```
[1] runtime loaded: 小鲤, pad=neutral, E=80
[2] 3 brain 已启动
[3] 模拟事件流:
  3.1 bilibili_msg → streamer → TTS + reply + state 更新 ✓
  3.2 gift_received → streamer → TTS + state 更新 ✓
  3.3 do_task → operator → operator_done → streamer 报告 ✓
  3.4 will_engine_action → streamer 宣布 + 触发 do_task ✓
[4] 最终 state: pad=excited (P=0.78, A=0.50, D=0.47)
[5] bus 统计: 21 emitted, 26 dispatched, 0 errors
=== V2 骨架验证完成 ===
```

### LLM 接入验证（base 环境）

```
API_KEY 设置: True
system_prompt 长度: 923  ← 包含 persona + state
真实 LLM response: （端点临时关闭，已验证错误处理）
```

---

## 6. V2 vs V1 对比

| 维度 | V1（LangGraph） | V2（EDA + 三脑） |
|------|------------------|------------------|
| 底层范式 | DAG | 事件驱动 |
| 主流程 | 单一链 | 无（事件流转） |
| 持续后台 | ❌ | ✅ |
| 主动决策 | ❌ | ✅ WillEngine |
| 多角色并行 | ⚠️ Send | ✅ 协程池 |
| 状态共享 | ⚠️ state dict | ✅ Runtime 单例 |
| 错误隔离 | try/except 节点 | bus.safe_invoke |
| 可测试性 | 中（整图测） | 高（每脑单测）|
| 调试 | 流程日志 | 事件流统计 |
| 复杂度 | 中（LangGraph 包装）| 低（纯 asyncio）|

---

## 7. V2 与 SkillWorld 集成

### 7.1 OperatorBrain 调用 SkillWorld 工具

```python
# 集成方式：OperatorBrain 内部用 SkillWorld 实例
class OperatorBrain(Brain):
    def __init__(self, runtime, skill_world=None):
        ...
        self._skill_world = skill_world or skill_assembly
    
    async def _on_do_task(self, payload):
        tool = payload["tool"]
        args = payload["args"]
        result = await self._skill_world.execute(tool, args)
        ...
```

### 7.2 域热启停（保留 V1 机制）

```python
# 通过 WillEngine 触发
await bus.emit(EVENTS.WORLD_TOGGLE, {"domain": "desktop", "action": "stop"})
```

---

## 8. LangGraph 迁移映射表

| 旧节点 | 新组件 |
|--------|--------|
| `init` | PersonaRuntime 单例 + main.py 启动 |
| `load_memory` | StreamerBrain 的 listener |
| `rag_retrieval` | StreamerBrain 的 listener（before LLM call） |
| `context_control` | StreamerBrain 的 `_build_system_prompt()` |
| `llm_process` | StreamerBrain 的 `_on_bilibili_msg()` |
| `finalize` | EventBus 自动持久化 + STATE_UPDATED 事件 |

---

## 9. 集成规范（新增能力的接入步骤）

### 9.1 新增 Brain
1. 在 `LLM_v2/brains/` 新建 `xxx_brain.py`
2. 继承 `Brain` 基类
3. 实现 `setup()` 注册监听器
4. 在 `main.py` DigitalBeing 类添加实例 + start
5. 同步更新本文档 §3.1 表格

### 9.2 新增事件
1. 在 `LLM_v2/events.py` 的 `EVENTS` 类添加常量
2. 分类到 `INPUT_EVENTS / OUTPUT_EVENTS / INTERNAL_EVENTS`
3. 同步更新本文档 §1.2 事件列表

### 9.3 新增工具
1. 实现工具函数（tool/{name}.py）
2. 在 COMPUTER_USE_TOOLS_SCHEMA 添加 schema
3. 在 SkillWorld 的 `_DESKTOP_TOOL_NAMES` 添加名字
4. 同步更新本文档 §1.2 工具统计

### 9.4 新增持久层存储
1. 实现 db.py
2. 同步更新本文档 §10

---

## 10. 持久层

| 存储 | 内容 | 实现位置 | 状态 |
|------|------|----------|------|
| Milvus | 向量长期记忆 | MilvusConnectionManager | ✅ |
| semantic_cache | 短期相似度缓存 | LLM/semantic_cache.py | ✅ |
| **sqlite (state.db)** | **PersonaState 动态状态** | **persona/state.py** | **✅** |
| sqlite (task_queue.db) | 任务队列 | 待新增（Phase E） | ❌ |
| sqlite (diary.db) | 日记索引 | 待新增（Phase E） | ❌ |
| config.ini | 系统配置 | config.ini | ✅ |
| skill_worlds.json | 能力域元数据 | LLM/skill_worlds.json | ✅ |

### 配置优先级
**环境变量 > config.ini > 代码默认值**（项目硬性约束）

---

## 11. 外部连接

| 连接 | 协议 | 实现位置 | 状态 |
|------|------|----------|------|
| Bilibili 弹幕 | WebSocket | BilibiliListener | ✅ 待改造为 event 发射器 |
| VTube Studio | WebSocket | live2d_main.py | ✅ 待改造为 event 监听器 |
| Milvus | gRPC | MilvusConnectionManager | ✅ |
| MinIO | S3 | config.ini | ✅ |
| GPT-SoVITS | HTTP | tts_node | ✅ |
| 豆包 / Qwen | HTTPS | chat_model.py → llm_adapter.py | ✅ |

### 本地 TTS 与云 TTS 互斥（项目硬性约束）
```
local_tts_enabled = true  → GPT-SoVITS
local_tts_enabled = false → 云 TTS
```

---

## 12. 安全边界（项目硬性约束）

| 不可为 | 措施 |
|--------|------|
| ❌ 删除用户文件 | 文件操作白名单 |
| ❌ 跑危险命令 | 命令黑名单 |
| ❌ 发不当弹幕 | 内容审核 |
| ❌ 外泄观众隐私 | 长期记忆脱敏 |

| 边界控制 | 触发条件 |
|----------|----------|
| Will 决策冷静期 | 决策后 30s 内不可改 |
| 自动暂停 | 任务失败 3 次后停 1h |
| 强制休息 | mood < 20 持续 30 分钟 |

---

## 13. 已知技术债

| 编号 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| TD-001 | IntentClassifier 硬编码 | P1 | ⚠️ Phase B 改造 |
| TD-002 | semantic_cache 缺重要性评分 | P1 | Phase 2 |
| TD-003 | 记忆无分层 | P1 | Phase 2 |
| TD-004 | 无反思机制 | P1 | **✅ V4 ReflectionBrain** |
| TD-005 | 无 Will Engine | P0 | ✅ V2 骨架完成 |
| TD-006 | browser 域简陋 | P1 | Phase 4 |
| TD-007 | 无 os_game_* 工具 | P2 | Phase 5 |
| TD-008 | 无 Persona + State | P0 | ✅ Phase 1 完成 |
| TD-009 | LangGraph 主流程存在 | P0 | ✅ V2 已抛弃 |
| TD-010 | OperatorBrain 接真实工具 | P0 | ✅ V2 完成 |
| TD-011 | Bilibili 监听器未事件化 | P1 | **✅ V4 完成** |
| TD-012 | 无 WebUI | P0 | **✅ V4 完成（FastAPI + 9 端点）** |
| TD-013 | 无 RAG 集成 | P1 | **✅ V4 完成（三因子评分）** |
| TD-014 | 无 context_controller | P1 | **✅ V4 完成（chat_with_context_control）** |
| TD-015 | 无反思 brain | P1 | **✅ V4 完成** |

---

## 14. 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v1.0 | 2026-08-29 | 初版（PROJECT_README.md） |
| v2.0 | 2026-10-14 | 升级为权威架构文档，新增 Persona + State |
| v3.0 | 2026-10-14 | V2 架构：抛弃 LangGraph → EDA + 单角色三脑 |
| **v4.0** | **2026-10-14** | **V2 完整迁移：4 脑架构（+Reflection）+ WebUI + RAG + context + 弹幕过滤/状态统计 + LangGraph 清理** |

---

## 附录：相关文档索引

- [AUTONOMOUS_AI_VISION.md](file:///e:/GitHub/LLM_vtuber/docs/AUTONOMOUS_AI_VISION.md) — 终极愿景 PRD
- [PERSONA_STATE_ARCHITECTURE.md](file:///e:/GitHub/LLM_vtuber/docs/PERSONA_STATE_ARCHITECTURE.md) — Persona + State 详细设计
- [REFACTOR_V2_PLAN.md](file:///e:/GitHub/LLM_vtuber/docs/REFACTOR_V2_PLAN.md) — V2 改造计划

---

**下次更新触发条件**：
- Phase B 完成（OperatorBrain 接工具）
- Phase C 完成（直播侧集成）
- Phase D 完成（删除 LangGraph）
- 新增任何 Brain / 事件 / 域 / 工具
