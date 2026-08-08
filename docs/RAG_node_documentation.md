# RAG 节点文档

## 1. 状态结构

### RAGState 状态定义

| 字段名 | 类型 | 默认值 | 说明 |
|-------|------|-------|------|
| `query_text` | str | - | 查询文本 |
| `collection_name` | str | "chat_history" | 目标集合名称 |
| `db_name` | str | "LLM_vtuber" | 数据库名称 |
| `query_params` | Optional[Dict] | None | 查询参数配置 |
| `messages` | Optional[list[AnyMessage]] | None | 对话历史，包含所有消息 |
| `context` | Optional[str] | None | 检索到的上下文信息 |
| `retrieved_documents` | Optional[List[Dict]] | None | 检索到的文档列表 |
| `output_results` | Optional[Dict] | None | 输出结果 |
| `response` | Optional[str] | None | 最新响应内容 |
| `error` | Optional[str] | None | 错误信息（如有） |
| `execution_time` | float | 0.0 | 执行耗时 |
| `retrieval_time` | float | 0.0 | 检索耗时 |
| `num_documents` | int | 0 | 检索到的文档数量 |
| `report` | Optional[Dict] | None | 生成的报告 |

### RetrievalParams 参数定义

| 字段名 | 类型 | 默认值 | 说明 |
|-------|------|-------|------|
| `top_k` | int | 3 | 返回结果数量 |
| `collection_name` | str | "chat_history" | 集合名称 |
| `db_name` | str | "LLM_vtuber" | 数据库名称 |
| `metric_type` | str | "COSINE" | 相似度度量方式 |
| `nprobe` | int | 10 | 搜索参数 |

## 2. 功能说明

### 核心功能

1. **文档检索**：通过 Milvus 向量数据库检索与查询文本相关的文档
2. **上下文构建**：将检索到的文档与原始查询整合成上下文信息
3. **结果处理**：生成结构化的输出结果和报告
4. **错误处理**：捕获并处理检索过程中的异常

### 主要函数

#### 1. `rag_retrieval_node(state: RAGState, retrieval_params: Optional[RetrievalParams] = None) -> RAGState`

**功能**：RAG 检索节点函数，执行文档检索操作并返回处理后的结果

**参数**：
- `state`：RAG 状态对象，包含对话历史和当前查询
- `retrieval_params`：检索参数，包含 top_k、collection_name 等配置

**返回值**：
- 更新后的 RAG 状态，包含检索结果和上下文信息

**处理流程**：
1. 记录开始时间
2. 解析检索参数
3. 提取查询文本（从 `query_text` 或 `messages` 中）
4. 初始化 Milvus 客户端和嵌入模型
5. 执行向量搜索
6. 处理检索结果
7. 构建上下文信息
8. 生成输出结果和报告
9. 计算执行耗时
10. 更新状态并返回

#### 2. `create_rag_context(retrieved_docs: List[Dict], query: str) -> str`

**功能**：将检索到的文档与原始查询整合成上下文信息

**参数**：
- `retrieved_docs`：检索到的文档列表
- `query`：用户查询

**返回值**：
- 整合后的上下文信息字符串

#### 3. `create_initial_rag_state() -> RAGState`

**功能**：创建初始 RAG 状态

**返回值**：
- 初始 RAG 状态对象

## 3. 数据流程

### 输入输出流程

1. **输入**：
   - 包含 `query_text` 的 RAGState 对象
   - 可选的 `retrieval_params` 参数

2. **处理**：
   - 解析参数
   - 执行向量搜索
   - 处理检索结果
   - 构建上下文
   - 生成报告

3. **输出**：
   - 更新后的 RAGState 对象，包含：
     - 检索到的文档列表
     - 构建的上下文信息
     - 输出结果
     - 生成的报告
     - 执行耗时
     - 错误信息（如有）

### 数据流向

```
输入状态 → 解析参数 → 执行检索 → 处理结果 → 构建上下文 → 生成报告 → 输出状态
```

## 4. 与 rag_retrieval_graph.py 的关系

### 状态结构对应

| RAG_node.py (RAGState) | rag_retrieval_graph.py (RetrievalState) |
|-----------------------|----------------------------------------|
| `query_text` | `query_text` |
| `collection_name` | `collection_name` |
| `db_name` | `db_name` |
| `query_params` | `query_params` |
| `retrieved_documents` | `retrieved_documents` |
| `output_results` | `output_results` |
| `error` | `error` |
| `execution_time` | `execution_time` |
| `report` | `report` |

### 功能对应

- **RAG_node.py**：提供单个检索节点功能，可作为 LangGraph 中的一个节点使用
- **rag_retrieval_graph.py**：构建完整的检索流程图，包含多个节点

### 数据格式兼容

RAG_node.py 的状态结构与 rag_retrieval_graph.py 的状态结构保持兼容，确保两者可以无缝集成。具体表现为：

1. 状态字段名称保持一致
2. 数据结构格式保持一致
3. 输出结果格式保持一致
4. 报告格式保持一致

## 5. 示例用法

### 基本用法

```python
from RAG.RAG_node import rag_retrieval_node, RAGState

# 创建初始状态
state = {
    "query_text": "Milvus是什么？",
    "collection_name": "chat_history",
    "db_name": "LLM_vtuber"
}

# 执行检索
result = rag_retrieval_node(state)

# 打印结果
print(f"检索到 {result['num_documents']} 个文档")
print(f"上下文信息: {result['context']}")
print(f"输出结果: {result['output_results']['summary']}")
```

### 高级用法（带参数）

```python
from RAG.RAG_node import rag_retrieval_node, RAGState, RetrievalParams

# 创建初始状态
state = {
    "query_text": "向量数据库有什么作用？",
    "collection_name": "chat_history",
    "db_name": "LLM_vtuber"
}

# 自定义检索参数
params = RetrievalParams(
    top_k=5,
    metric_type="COSINE",
    nprobe=20
)

# 执行检索
result = rag_retrieval_node(state, params)

# 打印结果
print(f"检索到 {result['num_documents']} 个文档")
print(f"检索耗时: {result['retrieval_time']:.2f} 秒")
print(f"上下文信息: {result['context']}")
```

### 从对话历史中提取查询

```python
from RAG.RAG_node import rag_retrieval_node, RAGState

# 创建包含对话历史的状态
state = {
    "collection_name": "chat_history",
    "db_name": "LLM_vtuber",
    "messages": [
        {"role": "user", "content": "你好，我想了解一下Milvus"},
        {"role": "assistant", "content": "Milvus是一个向量数据库"},
        {"role": "user", "content": "Milvus支持哪些向量索引类型？"}
    ]
}

# 执行检索（会自动从messages中提取最后一条用户消息作为查询）
result = rag_retrieval_node(state)

# 打印结果
print(f"检索到 {result['num_documents']} 个文档")
print(f"上下文信息: {result['context']}")
```

## 6. 错误处理

RAG_node.py 包含完善的错误处理机制，能够捕获并处理检索过程中的各种异常，确保系统稳定性。主要错误处理包括：

1. **查询文本缺失**：当 `query_text` 为空且无法从 `messages` 中提取时，会抛出 ValueError
2. **Milvus 连接错误**：处理 Milvus 客户端初始化失败的情况
3. **向量搜索错误**：处理搜索过程中的异常
4. **结果处理错误**：处理结果处理和报告生成过程中的异常

当发生错误时，函数会返回包含错误信息的状态对象，确保调用方能够获取到错误详情。

## 7. 性能优化

1. **参数缓存**：通过状态对象传递参数，避免重复初始化
2. **批量处理**：一次处理多个检索结果
3. **耗时统计**：详细记录检索和执行耗时，便于性能分析
4. **日志记录**：提供详细的日志信息，便于问题排查

## 8. 代码优化建议

1. **异步支持**：考虑添加异步版本的检索函数，提高并发处理能力
2. **缓存机制**：添加检索结果缓存，减少重复检索
3. **参数验证**：增加参数验证逻辑，确保输入参数的有效性
4. **可扩展性**：设计更灵活的接口，支持不同的向量数据库和嵌入模型
5. **监控指标**：添加更多监控指标，便于系统监控和性能分析

## 9. 总结

RAG_node.py 是一个功能完善的 RAG 检索节点实现，与 rag_retrieval_graph.py 保持数据格式兼容，可作为 LangGraph 中的一个核心节点使用。它提供了文档检索、上下文构建、结果处理和错误处理等功能，能够满足各种 RAG 场景的需求。

通过本文档的说明，开发者可以快速了解 RAG_node.py 的状态结构、功能和数据流程，从而更好地集成和使用该模块。