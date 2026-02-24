# 基于Milvus的虚拟AI聊天记录存储结构设计

## 1. 系统概述

### 1.1 系统目的

本设计旨在为虚拟AI角色（如VTuber）的聊天记录提供一个高效、可扩展的存储方案，基于Milvus向量数据库实现以下目标：

- 高效存储和检索大量聊天历史记录
- 支持基于语义相似度的智能搜索
- 为AI角色提供长期记忆和上下文理解能力
- 确保数据的可靠性和安全性
- 提供灵活的数据管理和分析能力

### 1.2 系统范围

本系统涵盖以下范围：

- 虚拟AI角色与用户之间的所有聊天记录
- AI角色的长期记忆和设定信息
- 会话级上下文摘要和管理
- 用户画像和偏好信息
- 相关的元数据和分析数据

### 1.3 关键需求

1. **高性能**：支持实时聊天记录的写入和查询
2. **可扩展性**：能够处理不断增长的聊天数据量
3. **语义搜索**：支持基于向量相似度的智能检索
4. **数据完整性**：确保聊天记录的准确性和完整性
5. **安全性**：保护用户隐私和敏感信息
6. **可管理性**：提供灵活的数据管理和维护能力

## 2. 数据模型设计

### 2.1 集合概述

#### 2.1.1 chat_history 集合

**内容概述**：存储所有虚拟AI角色与用户之间的聊天历史记录，是系统的核心数据集合。

**核心功能**：
- 记录完整的聊天对话历史
- 支持按用户、角色、时间等维度查询
- 提供基于语义相似度的消息检索
- 为会话上下文提供数据基础

**整体结构**：
- 按月份分区，每个月创建一个分区
- 包含消息基本信息、内容向量和元数据
- 支持动态字段，适应不同类型的消息内容

#### 2.1.2 role_memory 集合

**内容概述**：存储虚拟AI角色的长期记忆和设定信息，是AI角色个性和知识的核心存储。

**核心功能**：
- 存储角色的核心设定和背景信息
- 记录角色的重要经历和记忆
- 支持基于语义相似度的记忆检索
- 为AI角色提供长期记忆能力

**整体结构**：
- 按角色ID分区，每个角色一个分区
- 包含记忆内容、类型和重要性级别
- 支持不同类型的记忆存储

#### 2.1.3 session_context 集合

**内容概述**：存储会话级别的上下文摘要和管理信息，提供会话的整体视图。

**核心功能**：
- 存储会话的摘要信息
- 记录会话的开始和结束时间
- 支持基于会话摘要的相似度搜索
- 为会话管理和分析提供数据基础

**整体结构**：
- 按季度分区，每个季度创建一个分区
- 包含会话基本信息、摘要内容和统计数据
- 支持会话级别的元数据存储

#### 2.1.4 user_profiles 集合

**内容概述**：存储用户的画像和偏好信息，为个性化交互提供数据支持。

**核心功能**：
- 存储用户的基本信息和偏好设置
- 记录用户的交互历史摘要
- 支持基于用户向量的相似度匹配
- 为个性化推荐和交互提供数据基础

**整体结构**：
- 按用户ID的哈希值分区，均匀分布数据
- 包含用户基本信息、偏好设置和交互历史
- 支持用户级别的元数据存储

### 2.2 字段详细信息

#### 2.2.1 chat_history 集合字段

| 字段名 | 数据类型 | 约束条件 | 描述 |
|-------|---------|---------|------|
| `message_id` | INT64 | 主键，自动生成 | 消息唯一标识符 |
| `session_id` | VARCHAR(64) | 非空 | 会话唯一标识符 |
| `role_id` | VARCHAR(64) | 非空 | AI角色唯一标识符 |
| `user_id` | VARCHAR(64) | 非空 | 用户唯一标识符 |
| `message_type` | VARCHAR(32) | 非空 | 消息类型（文本/图像/音频） |
| `content` | VARCHAR(4096) | 非空 | 消息内容 |
| `content_vector` | FLOAT_VECTOR | 非空，维度2560 | 消息内容的向量表示 |
| `timestamp` | TIMESTAMP | 非空 | 消息发送时间 |
| `context_relevance` | FLOAT | 可空 | 与上下文的相关度 |
| `is_important` | BOOL | 可空 | 是否为重要消息 |
| `metadata` | JSON | 可空 | 额外的元数据信息 |

#### 2.2.2 role_memory 集合字段

| 字段名 | 数据类型 | 约束条件 | 描述 |
|-------|---------|---------|------|
| `memory_id` | VARCHAR(64) | 主键 | 记忆唯一标识符 |
| `role_id` | VARCHAR(64) | 非空 | AI角色唯一标识符 |
| `memory_type` | VARCHAR(32) | 非空 | 记忆类型（核心设定/事件记忆/关系记忆） |
| `content` | VARCHAR(4096) | 非空 | 记忆内容 |
| `content_vector` | FLOAT_VECTOR | 非空，维度2560 | 记忆内容的向量表示 |
| `timestamp` | TIMESTAMP | 非空 | 记忆创建/更新时间 |
| `importance_level` | INT32 | 可空 | 重要性级别（1-5） |
| `metadata` | JSON | 可空 | 额外的元数据信息 |

#### 2.2.3 session_context 集合字段

| 字段名 | 数据类型 | 约束条件 | 描述 |
|-------|---------|---------|------|
| `session_id` | VARCHAR(64) | 主键 | 会话唯一标识符 |
| `role_id` | VARCHAR(64) | 非空 | AI角色唯一标识符 |
| `user_id` | VARCHAR(64) | 非空 | 用户唯一标识符 |
| `session_summary` | VARCHAR(4096) | 非空 | 会话内容摘要 |
| `summary_vector` | FLOAT_VECTOR | 非空，维度2560 | 会话摘要的向量表示 |
| `start_time` | TIMESTAMP | 非空 | 会话开始时间 |
| `end_time` | TIMESTAMP | 可空 | 会话结束时间 |
| `message_count` | INT32 | 可空 | 消息数量 |
| `metadata` | JSON | 可空 | 额外的元数据信息 |

#### 2.2.4 user_profiles 集合字段

| 字段名 | 数据类型 | 约束条件 | 描述 |
|-------|---------|---------|------|
| `user_id` | VARCHAR(64) | 主键 | 用户唯一标识符 |
| `username` | VARCHAR(100) | 非空 | 用户名 |
| `user_preferences` | JSON | 可空 | 用户偏好设置 |
| `interaction_history` | JSON | 可空 | 交互历史摘要 |
| `last_interaction_time` | TIMESTAMP | 可空 | 最后交互时间 |
| `user_vector` | FLOAT_VECTOR | 非空，维度2560 | 用户画像的向量表示 |
| `metadata` | JSON | 可空 | 额外的元数据信息 |

### 2.3 索引方式

#### 2.3.1 chat_history 集合索引

| 字段名 | 索引类型 | 索引方向 | 适用场景 |
|-------|---------|---------|----------|
| `message_id` | 主键索引 | 升序 | 唯一标识和快速查找单条消息 |
| `session_id` | 单字段索引 (STL_SORT) | 升序 | 会话内消息查询，按会话ID过滤 |
| `role_id` | 单字段索引 (STL_SORT) | 升序 | 特定角色消息查询，按角色ID过滤 |
| `user_id` | 单字段索引 (STL_SORT) | 升序 | 特定用户消息查询，按用户ID过滤 |
| `message_type` | 单字段索引 (STL_SORT) | 升序 | 特定类型消息查询，按消息类型过滤 |
| `timestamp` | 单字段索引 (STL_SORT) | 升序 | 时间范围查询，按时间排序 |
| `content_vector` | 向量索引 (HNSW) | COSINE相似度 | 语义搜索，相似消息查找 |

#### 2.3.2 role_memory 集合索引

| 字段名 | 索引类型 | 索引方向 | 适用场景 |
|-------|---------|---------|----------|
| `memory_id` | 主键索引 | 升序 | 唯一标识和快速查找单条记忆 |
| `role_id` | 单字段索引 (STL_SORT) | 升序 | 特定角色记忆查询，按角色ID过滤 |
| `content_vector` | 向量索引 (HNSW) | COSINE相似度 | 角色记忆检索，相关记忆联想 |

#### 2.3.3 session_context 集合索引

| 字段名 | 索引类型 | 索引方向 | 适用场景 |
|-------|---------|---------|----------|
| `session_id` | 主键索引 | 升序 | 唯一标识和快速查找单个会话 |
| `role_id` | 单字段索引 (STL_SORT) | 升序 | 特定角色会话查询，按角色ID过滤 |
| `user_id` | 单字段索引 (STL_SORT) | 升序 | 特定用户会话查询，按用户ID过滤 |
| `summary_vector` | 向量索引 (HNSW) | COSINE相似度 | 会话摘要检索，上下文理解 |

#### 2.3.4 user_profiles 集合索引

| 字段名 | 索引类型 | 索引方向 | 适用场景 |
|-------|---------|---------|----------|
| `user_id` | 主键索引 | 升序 | 唯一标识和快速查找单个用户 |
| `username` | 单字段索引 (STL_SORT) | 升序 | 按用户名查询用户信息 |
| `user_vector` | 向量索引 (HNSW) | COSINE相似度 | 用户相似度匹配，个性化推荐 |

## 3. 分区策略

为了提高查询性能和管理效率，采用以下分区策略：

### 3.1 按时间分区

- **chat_history 集合**：按月份分区，每个月创建一个分区
- **session_context 集合**：按季度分区，每个季度创建一个分区

### 3.2 按角色分区

- **role_memory 集合**：按角色ID分区，每个角色一个分区

### 3.3 按用户分区

- **user_profiles 集合**：按用户ID的哈希值分区，均匀分布数据

## 4. 数据摄入流程

### 4.1 数据格式规范

#### 4.1.1 聊天记录格式

```json
{
  "session_id": "session_20241201_001",
  "role_id": "vtuber_001",
  "user_id": "user_123",
  "message_type": "text",
  "content": "你好，今天过得怎么样？",
  "content_vector": [0.123, 0.456, ...],  // 2560维向量
  "timestamp": 1733049600000,  // 毫秒时间戳
  "context_relevance": 0.95,
  "is_important": false,
  "metadata": {
    "message_source": "web_chat",
    "device_type": "desktop"
  }
}
```

#### 4.1.2 角色记忆格式

```json
{
  "memory_id": "memory_001",
  "role_id": "vtuber_001",
  "memory_type": "core_setting",
  "content": "我是一个活泼开朗的虚拟主播，喜欢唱歌和玩游戏。",
  "content_vector": [0.789, 0.234, ...],  // 2560维向量
  "timestamp": 1733049600000,
  "importance_level": 5,
  "metadata": {
    "memory_source": "initial_setting",
    "created_by": "admin"
  }
}
```

#### 4.1.3 会话上下文格式

```json
{
  "session_id": "session_20241201_001",
  "role_id": "vtuber_001",
  "user_id": "user_123",
  "session_summary": "用户询问了今天的天气情况，我提供了详细的天气预报。",
  "summary_vector": [0.567, 0.890, ...],  // 2560维向量
  "start_time": 1733049600000,
  "end_time": 1733053200000,
  "message_count": 10,
  "metadata": {
    "session_duration": 3600,
    "topic": "weather"
  }
}
```

#### 4.1.4 用户画像格式

```json
{
  "user_id": "user_123",
  "username": "JohnDoe",
  "user_preferences": {
    "favorite_topics": ["gaming", "music", "technology"],
    "preferred_communication_style": "friendly"
  },
  "interaction_history": {
    "total_sessions": 25,
    "average_session_duration": 1800,
    "last_session_date": "2024-12-01"
  },
  "last_interaction_time": 1733053200000,
  "user_vector": [0.345, 0.678, ...],  // 2560维向量
  "metadata": {
    "user_level": "premium",
    "join_date": "2024-01-01"
  }
}
```

### 4.2 批量vs流式摄入

| 摄入方式 | 适用场景 | 优点 | 缺点 | 推荐配置 |
|---------|---------|------|------|----------|
| 批量摄入 | 历史数据导入，定期同步 | 吞吐量高，资源利用合理 | 延迟较高 | 批次大小：1000-5000条/批 |
| 流式摄入 | 实时聊天记录，在线更新 | 实时性强，响应迅速 | 资源消耗较高 | 单条或小批量（<100条） |

### 4.3 数据验证和预处理

1. **数据验证**：
   - 检查必填字段是否存在
   - 验证向量维度是否正确
   - 检查时间戳格式是否有效
   - 验证字符串长度是否符合限制

2. **数据预处理**：
   - 文本内容清洗和标准化
   - 向量归一化处理
   - 时间戳统一转换为毫秒格式
   - 元数据JSON格式验证

3. **错误处理**：
   - 记录摄入失败的数据
   - 实现重试机制
   - 提供错误报告和监控

## 5. 查询模式和示例

### 5.1 常见查询场景

#### 5.1.1 按用户查询

**场景描述**：查询特定用户的所有聊天记录

**示例代码**：

```python
from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://localhost:19530",
    token="root:Milvus",
    db_name="LLM_vtuber"
)

# 查询特定用户的聊天记录
results = client.query(
    collection_name="chat_history",
    filter="user_id == 'user_123'",
    output_fields=["message_id", "content", "timestamp", "role_id"],
    limit=100,
    offset=0
)

# 按时间排序
results.sort(key=lambda x: x["timestamp"])
```

#### 5.1.2 按时间范围查询

**场景描述**：查询特定时间段内的聊天记录

**示例代码**：

```python
# 查询2024年12月1日的聊天记录
start_time = 1733049600000  # 2024-12-01 00:00:00
end_time = 1733136000000    # 2024-12-02 00:00:00

results = client.query(
    collection_name="chat_history",
    filter=f"timestamp >= {start_time} AND timestamp < {end_time}",
    output_fields=["message_id", "content", "timestamp", "user_id"],
    limit=500,
    offset=0
)
```

#### 5.1.3 语义相似度查询

**场景描述**：查询与给定内容语义相似的聊天记录

**示例代码**：

```python
# 假设我们有一个嵌入模型
from LLM.chat_model import DoubaoEmbeddings

embedding_model = DoubaoEmbeddings()

# 生成查询向量
query_text = "今天天气怎么样？"
query_vector = embedding_model.embed_query(query_text)

# 执行相似度搜索
results = client.search(
    collection_name="chat_history",
    data=[query_vector],
    limit=10,
    output_fields=["message_id", "content", "timestamp", "role_id"],
    search_params={"metric_type": "COSINE", "params": {"ef": 100}}
)

# 处理搜索结果
for hits in results:
    for hit in hits:
        print(f"相似度: {hit.score:.4f}, 内容: {hit.entity['content']}")
```

#### 5.1.4 会话上下文查询

**场景描述**：查询特定会话的上下文信息

**示例代码**：

```python
# 查询特定会话的上下文
results = client.query(
    collection_name="session_context",
    filter="session_id == 'session_20241201_001'",
    output_fields=["session_summary", "start_time", "end_time", "message_count"],
    limit=1
)

# 查询该会话的聊天记录
chat_results = client.query(
    collection_name="chat_history",
    filter="session_id == 'session_20241201_001'",
    output_fields=["message_id", "content", "timestamp", "role_id", "user_id"],
    limit=100
)

# 按时间排序
chat_results.sort(key=lambda x: x["timestamp"])
```

#### 5.1.5 角色记忆检索

**场景描述**：查询与当前对话相关的角色记忆

**示例代码**：

```python
# 生成查询向量
current_dialog = "我想了解你的兴趣爱好"
query_vector = embedding_model.embed_query(current_dialog)

# 搜索相关的角色记忆
results = client.search(
    collection_name="role_memory",
    data=[query_vector],
    filter="role_id == 'vtuber_001'",
    limit=5,
    output_fields=["memory_id", "content", "memory_type", "importance_level"],
    search_params={"metric_type": "COSINE", "params": {"ef": 100}}
)

# 处理搜索结果
for hits in results:
    for hit in hits:
        print(f"相似度: {hit.score:.4f}, 类型: {hit.entity['memory_type']}, 内容: {hit.entity['content']}")
```

### 5.2 查询性能考虑

| 查询类型 | 预期响应时间 | 优化策略 | 限制因素 |
|---------|------------|---------|----------|
| 标量过滤查询 | < 100ms | 使用适当的标量索引 | 数据量大小 |
| 语义相似度查询 | < 500ms | 调整ef参数，使用批处理 | 向量维度和数据量 |
| 复合查询（过滤+搜索） | < 1000ms | 先过滤后搜索，合理设置limit | 过滤条件复杂度 |
| 大范围时间查询 | < 2000ms | 使用时间分区，合理设置offset | 时间范围大小 |

## 6. 数据保留和管理

### 6.1 TTL配置

| 集合名称 | 数据类型 | TTL设置 | 清理策略 |
|---------|---------|---------|----------|
| chat_history | 聊天记录 | 90天 | 自动清理超过90天的记录 |
| session_context | 会话上下文 | 180天 | 自动清理超过180天的会话 |
| role_memory | 角色记忆 | 无限制 | 手动管理 |
| user_profiles | 用户画像 | 无限制 | 手动管理 |

### 6.2 归档策略

1. **热数据**：最近30天的聊天记录，保留在主集合中

2. **温数据**：30-90天的聊天记录，迁移到归档集合

3. **冷数据**：超过90天的聊天记录，存储到对象存储

4. **归档流程**：
   - 定期执行归档任务
   - 使用分区管理简化数据迁移
   - 维护归档索引以支持历史查询

### 6.3 备份和恢复

1. **备份策略**：
   - 每日增量备份
   - 每周全量备份
   - 备份存储在异地

2. **恢复流程**：
   - 建立详细的恢复步骤文档
   - 定期测试恢复流程
   - 制定RTO（恢复时间目标）和RPO（恢复点目标）

3. **灾难恢复**：
   - 实现跨区域复制
   - 建立备用Milvus集群
   - 制定灾难恢复计划

## 7. 安全考虑

### 7.1 访问控制机制

1. **认证**：
   - 使用令牌认证机制
   - 实现API密钥管理
   - 定期轮换凭证

2. **授权**：
   - 基于角色的访问控制（RBAC）
   - 细粒度的权限管理
   - 最小权限原则

3. **审计**：
   - 记录所有数据访问操作
   - 定期审计访问日志
   - 监控异常访问模式

### 7.2 数据加密

1. **传输加密**：
   - 使用TLS/SSL加密传输
   - 验证服务器证书

2. **存储加密**：
   - 实现数据-at-rest加密
   - 安全管理加密密钥

3. **敏感数据处理**：
   - 实现数据脱敏
   - 对敏感字段进行特殊处理

### 7.3 合规性

1. **数据保护法规**：
   - 遵守GDPR（欧盟）
   - 遵守CCPA（加州）
   - 遵守当地数据保护法规

2. **数据主体权利**：
   - 支持数据访问请求
   - 支持数据删除请求
   - 支持数据导出请求

3. **隐私政策**：
   - 明确的数据收集和使用政策
   - 获得用户同意
   - 提供隐私设置选项

## 8. 实现指南

### 8.1 Milvus配置建议

| 配置项 | 建议值 | 说明 |
|-------|-------|------|
| `minio.bucket_name` | milvus-bucket | MinIO存储桶名称 |
| `rocksmq.retention_period` | 43200000 | 消息队列保留时间（12小时） |
| `storage.max_size` | 858993459200 | 存储最大容量（800GB） |
| `cache.insert_buffer_size` | 10737418240 | 插入缓冲区大小（10GB） |
| `cache.query_segment_cache_size` | 21474836480 | 查询段缓存大小（20GB） |

### 8.2 硬件资源要求

#### 8.2.1 开发环境

| 资源类型 | 配置要求 |
|---------|---------|
| CPU | 8核以上 |
| 内存 | 16GB以上 |
| 存储 | 200GB SSD |
| 网络 | 千兆以太网 |

#### 8.2.2 生产环境

| 资源类型 | 配置要求 |
|---------|---------|
| CPU | 16核以上 |
| 内存 | 64GB以上 |
| 存储 | 1TB SSD |
| 网络 | 万兆以太网 |

### 8.3 监控和维护

1. **监控指标**：
   - 查询延迟和吞吐量
   - 数据摄入速率
   - 内存和CPU使用率
   - 存储使用情况
   - 错误率和异常情况

2. **告警机制**：
   - 设置合理的告警阈值
   - 实现多渠道告警（邮件、短信、即时通讯）
   - 建立告警升级流程

3. **维护任务**：
   - 定期优化索引
   - 清理过期数据
   - 备份验证
   - 性能测试和基准测试

4. **故障排查**：
   - 建立故障排查手册
   - 实现日志聚合和分析
   - 提供远程诊断工具

## 9. 代码示例

### 9.1 集合创建示例

```python
from pymilvus import MilvusClient, DataType, IndexParams

# 初始化客户端
client = MilvusClient(
    uri="http://localhost:19530",
    token="root:Milvus",
    db_name="LLM_vtuber"
)

# 创建聊天记录集合
def create_chat_history_collection():
    # 检查集合是否存在
    if client.has_collection("chat_history"):
        client.drop_collection("chat_history")
    
    # 创建schema
    schema = client.create_schema(
        auto_id=True,
        enable_dynamic_field=True
    )
    
    # 添加字段
    schema.add_field(
        field_name="message_id",
        datatype=DataType.INT64,
        is_primary=True,
        auto_id=True
    )
    
    schema.add_field(
        field_name="session_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="role_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="user_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="message_type",
        datatype=DataType.VARCHAR,
        max_length=32,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=4096,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=2560,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="timestamp",
        datatype=DataType.TIMESTAMP,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="context_relevance",
        datatype=DataType.FLOAT,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="is_important",
        datatype=DataType.BOOL,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="metadata",
        datatype=DataType.JSON,
        is_nullable=True
    )
    
    # 创建集合
    client.create_collection(
        collection_name="chat_history",
        schema=schema,
        consistency_level="Strong"
    )
    
    # 创建向量索引
    vector_index_params = IndexParams(
        index_type="HNSW",
        metric_type="COSINE",
        params={
            "M": 16,
            "efConstruction": 200
        }
    )
    
    client.create_index(
        collection_name="chat_history",
        field_name="content_vector",
        index_params=vector_index_params
    )
    
    # 创建标量索引
    session_index_params = IndexParams(
        index_type="STL_SORT"
    )
    
    client.create_index(
        collection_name="chat_history",
        field_name="session_id",
        index_params=session_index_params
    )
    
    print("聊天记录集合创建成功！")

# 调用函数创建集合
create_chat_history_collection()
```

### 9.2 数据插入示例

```python
import json
from datetime import datetime

# 生成示例聊天记录
def generate_sample_chat_data():
    data = []
    
    # 生成10条示例记录
    for i in range(10):
        # 模拟向量（实际应用中应使用真实的嵌入模型）
        vector = [0.1 * i for _ in range(2560)]
        
        record = {
            "session_id": "session_20241201_001",
            "role_id": "vtuber_001",
            "user_id": "user_123",
            "message_type": "text",
            "content": f"这是第{i+1}条测试消息",
            "content_vector": vector,
            "timestamp": int(datetime.now().timestamp() * 1000),
            "context_relevance": 0.8 + (i * 0.02),
            "is_important": i % 3 == 0,
            "metadata": {
                "message_source": "test",
                "sequence_number": i + 1
            }
        }
        
        data.append(record)
    
    return data

# 插入数据
def insert_chat_data():
    # 生成示例数据
    data = generate_sample_chat_data()
    
    # 插入数据
    result = client.insert(
        collection_name="chat_history",
        data=data
    )
    
    print(f"成功插入 {len(result['ids'])} 条记录")

# 调用函数插入数据
insert_chat_data()
```

### 9.3 数据查询示例

```python
# 执行相似度搜索
def search_similar_messages(query_text, top_k=5):
    # 假设我们有一个嵌入模型
    from LLM.chat_model import DoubaoEmbeddings
    embedding_model = DoubaoEmbeddings()
    
    # 生成查询向量
    query_vector = embedding_model.embed_query(query_text)
    
    # 执行搜索
    results = client.search(
        collection_name="chat_history",
        data=[query_vector],
        limit=top_k,
        output_fields=["message_id", "content", "timestamp", "role_id"],
        search_params={"metric_type": "COSINE", "params": {"ef": 100}}
    )
    
    # 打印结果
    print(f"与 '{query_text}' 相似的消息:")
    for i, hits in enumerate(results):
        for j, hit in enumerate(hits):
            print(f"\n排名 {j+1}:")
            print(f"相似度: {hit.score:.4f}")
            print(f"内容: {hit.entity['content']}")
            print(f"时间: {hit.entity['timestamp']}")
            print(f"角色: {hit.entity['role_id']}")

# 调用函数执行搜索
search_similar_messages("你好，今天过得怎么样？")
```

## 10. 总结

本设计文档提供了一个基于Milvus的虚拟AI聊天记录存储结构的完整方案，涵盖了从数据模型设计到实现细节的各个方面。通过合理的集合设计、索引策略和数据管理方案，本系统能够高效存储和检索大量聊天记录，支持基于语义相似度的智能搜索，并为AI角色提供长期记忆和上下文理解能力。

### 10.1 设计优势

1. **高效的向量存储和检索**：利用Milvus的向量索引能力，实现快速的语义搜索
2. **灵活的数据模型**：支持结构化和非结构化数据，适应各种聊天场景
3. **可扩展的架构**：通过分区策略和索引优化，支持数据量的增长
4. **智能的记忆管理**：为AI角色提供长期记忆和上下文理解能力
5. **完善的安全和合规**：保护用户隐私和敏感信息

### 10.2 未来扩展

1. **多模态支持**：扩展到图像、音频等多模态聊天内容
2. **实时分析**：添加实时聊天分析和情感识别
3. **知识图谱集成**：构建角色和用户之间的知识图谱
4. **自动摘要**：实现聊天内容的自动摘要和总结
5. **个性化推荐**：基于用户画像和聊天历史的个性化内容推荐

通过本设计方案，可以构建一个功能强大、性能优异的虚拟AI聊天记录存储系统，为虚拟AI角色的智能交互提供坚实的技术基础。