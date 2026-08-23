# Memory 节点修复与主流程集成计划

## 一、现状分析

### 1.1 当前架构

**LangGraph 主流程**：`init → rag_retrieval → llm_process → rag_save → (live2d) → (tts) → finalize`

**状态类型**：
- `ChatState`（基类）：messages, system_prompt, response, model, temperature, max_tokens, error 等
- `LLMState`（继承 ChatState）：+ question, context, name

### 1.2 memory_node.py 问题清单

| # | 类型 | 问题 | 严重度 |
|---|------|------|--------|
| 1 | **Bug** | `MemoryState.messages` 使用 `list[AnyMessage]`，但 `AnyMessage` 在 typing_extensions 中不存在（应为 `list[Any]`） | 高 |
| 2 | **Bug** | `memory_node()` 是同步函数，但整个 LangGraph 流程是 async 的，会阻塞事件循环 | 高 |
| 3 | **Bug** | 每次调用都生成新的 `session_id`（`uuid.uuid4()`），无法跨轮次关联对话 | 高 |
| 4 | **Bug** | `store_response()` 未检查 `chat_history` 集合是否存在就直接 insert，失败时静默 | 中 |
| 5 | **Bug** | `VECTOR_DIM = 2560` 硬编码，豆包 Embeddings 实际维度可能不同，导致维度不匹配 | 中 |
| 6 | **健壮性** | 无重试机制，网络抖动会导致整个存储丢失 | 中 |
| 7 | **健壮性** | `embed_query()` 是阻塞调用，无超时保护 | 中 |
| 8 | **集成** | `memory_node` 完全未接入 LangGraph 主流程，独立运行无意义 | 高 |
| 9 | **集成** | 与 `rag_save_node` 功能重叠（都写 `chat_history` 集合），但数据格式不同 | 中 |

### 1.3 memory_node 与 rag_save_node 的职责差异

| 维度 | rag_save_node | memory_node (待修复) |
|------|---------------|---------------------|
| 存储内容 | 完整对话消息列表 | 仅 AI 回答 + 元数据 |
| 集合 | `chat_history` | `chat_history`（相同） |
| 目的 | RAG 检索用 | 长期记忆持久化 |
| 状态类型 | RAGState | MemoryState |
| 异步 | ❌ 同步 | ❌ 同步（待改为 async） |

## 二、修复方案

### 步骤 1：修复 memory_node.py Bug 和健壮性

#### 1.1 修复类型定义
```python
# 修改前
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage

class MemoryState(TypedDict):
    messages: list[AnyMessage]  # ❌ AnyMessage 不存在

# 修改后
class MemoryState(TypedDict):
    messages: list[Any]  # ✅ 使用 typing.Any
```

#### 1.2 改为异步实现
- `connect()` → `async_connect()`（使用 asyncio.to_thread 包裹 Milvus 操作）
- `store_response()` → `async_store_response()`（使用 asyncio.to_thread 包裹）
- `memory_node()` → `async_memory_node()`（异步入口）

#### 1.3 添加集合检查和自动创建
- 在 `store_response()` 前检查 `chat_history` 集合是否存在
- 不存在则自动创建，含必要的字段（session_id, user_id, content, content_vector 等）

#### 1.4 会话 ID 透传
- `session_id` 不再在 memory_node 内生成，改为从 state 中读取
- 如果 state 中没有 session_id，则 fallback 到 thread_id 或生成新 ID

#### 1.5 向量维度自适应
- 不再硬编码 `VECTOR_DIM = 2560`
- 从实际 embedding 结果自动检测维度
- 存储时动态调整

#### 1.6 添加重试和超时
- Milvus 操作添加最多 2 次重试
- embed_query 添加超时保护（30s）

### 步骤 2：接入 LangGraph 主流程

#### 2.1 在 LangGraph 中添加 memory 节点
在 `build_graph()` 中，在 `rag_save` 之后、`live2d/tts/finalize` 之前添加 `memory` 节点：

```
init → rag_retrieval → llm_process → rag_save → memory → (live2d) → (tts) → finalize
```

#### 2.2 添加 memory 节点实现
在 `LangGraphManager` 中添加 `_memory_node()` 方法：
- 从 `LLMState` 提取必要字段构建 `MemoryState`
- 调用异步版 `memory_node()`
- 将存储结果（storage_success, storage_time）写回 `LLMState`

#### 2.3 透传 session_id
在 `run_with_messages()` 中将 `thread_id` 作为 `session_id` 注入 state，确保同一会话的多轮对话使用相同的 session_id。

### 步骤 3：合理安排运行流程

**修复后的流程**：
```
init → rag_retrieval → llm_process → rag_save → memory → (live2d) → (tts) → finalize
```

各节点职责：
| 节点 | 职责 | 失败影响 |
|------|------|----------|
| init | 初始化状态、注入 enable_live2d | 阻断 |
| rag_retrieval | 从 chat_history 检索历史上下文 | 降级（无历史） |
| llm_process | 调用 LLM 生成回答 | 阻断 |
| rag_save | 保存对话到 RAG 集合（供后续检索） | 降级（不保存） |
| **memory** | **保存 AI 回答到记忆集合（长期记忆）** | **降级（不保存，不阻断）** |
| live2d | 控制 Live2D 动作 | 降级 |
| tts | TTS 语音播放 | 降级 |
| finalize | 最终处理 | 阻断 |

**关键设计**：memory 节点失败**不阻断**主流程（best-effort），仅记录日志。

### 步骤 4：验证

1. 运行 memory_node.py 独立测试
2. 启动主流程发送测试消息，验证 memory 节点被调用
3. 检查 Milvus 中 chat_history 集合是否正确存储
4. 多轮对话验证 session_id 一致性
5. 故障注入（断开 Milvus）验证降级行为

## 三、涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory_node.py` | 重写 | 修复所有 Bug、改为异步、添加集合检查/重试/超时 |
| `main.py` | 编辑 | 在 LangGraph 中添加 memory 节点、透传 session_id |

## 四、风险与注意事项

1. **向后兼容**：`memory_node` 改为异步后，调用方需使用 `await`
2. **与 rag_save 重叠**：两个节点都写 `chat_history`，但数据格式不同。后续可考虑合并，但本次先并行运行
3. **性能**：embed_query 是阻塞操作，使用 `asyncio.to_thread` 不影响主事件循环
4. **降级策略**：memory 节点失败仅记录日志，不阻断主流程
5. **Milvus 依赖**：需要确保 Milvus 服务运行，否则 memory 节点自动降级
