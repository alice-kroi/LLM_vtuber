# 回复效率与 Agent 优化方案

## 一、现状分析

### 1.1 当前执行流程

```
用户消息
    ↓
init (初始化)
    ↓
load_memory (加载历史记忆)  ← 串行阻塞
    ↓
rag_retrieval (RAG 检索)    ← Milvus 查询
    ↓
context_control (上下文控制) ← Token 裁剪/压缩
    ↓
llm_process (LLM 调用)      ← 主要瓶颈 (2-5秒)
    ↓                         ← 可能含 1-3 轮工具调用
rag_save (保存到 Milvus)    ← 串行阻塞
    ↓
save_memory (保存到内存)    ← 串行阻塞
    ↓
[live2d] (动作生成)         ← 串行阻塞
    ↓
[tts/cloud_tts] (语音合成) ← 串行阻塞
    ↓
finalize (结束)
```

### 1.2 性能瓶颈分析

| 阶段 | 耗时估算 | 瓶颈原因 |
|------|---------|---------|
| load_memory | 100-300ms | 内存/数据库查询 |
| rag_retrieval | 200-500ms | Milvus 向量检索 |
| context_control | 100-300ms | Token 计算、历史压缩 |
| **llm_process** | **2000-5000ms** | **LLM API 调用 (主要瓶颈)** |
| rag_save | 200-500ms | Milvus 写入 |
| save_memory | 50-100ms | 内存写入 |
| live2d | 50-100ms | 参数计算 |
| cloud_tts | 1000-3000ms | 云端 TTS API 调用 |

**总耗时估算：3.7-10秒**

### 1.3 Agent 现状问题

1. **响应过长**：LLM 经常生成冗长回复，浪费 token 和时间
2. **格式冗余**：回复包含 Markdown 符号、列表标记、表情符号等
3. **思考过程泄漏**：开启思考模式时 `reasoning_content` 混入正式回复
4. **工具调用低效**：最多 3 轮工具调用 + 最终生成，可能耗时 10+ 秒
5. **无流式输出**：用户需等待完整流程完成才能看到回复

---

## 二、优化方案

### 2.1 架构优化 - 并行化改造

#### 方案 A：后处理并行化（推荐优先实施）

**核心思想**：LLM 返回结果后，立即推送给用户，同时并行执行存储和 TTS。

```
                    ┌→ rag_save ─→┐
llm_process → 立即推送回复 → save_memory → finalize
                    └→ live2d  ─→┘
                    └→ tts     ─→┘
```

**改造点**：
- `llm_process` 完成后立即通过 WebSocket 推送回复
- `rag_save`、`save_memory`、`live2d`、`tts` 并行执行
- 用户首字响应时间从 ~5s 降至 ~2s（仅 LLM 调用时间）

**实现方式**：
```python
# 改造后的图结构
graph.add_edge("llm_process", END)  # LLM 完成即结束主流程

# 同时启动后台异步任务
asyncio.gather(
    rag_save_task(),      # 后台保存
    live2d_task(),        # 后台动作
    tts_task(),           # 后台 TTS
)
```

#### 方案 B：流水线并行（进阶）

**核心思想**：在 LLM 流式生成过程中，边生成边处理。

```
LLM Streaming:  Token1 → Token2 → Token3 → ... → TokenN
                    ↓         ↓         ↓          ↓
WebSocket推送:   Token1  Token2  Token3  ...  TokenN
                    ↓         ↓         ↓          ↓
TTS合成:        [Start]  [Chunk1] [Chunk2] ... [Final]
                    ↓         ↓         ↓          ↓
音频播放:                 [Play1]   [Play2] ... [PlayN]
```

**改造点**：
- LLM 使用流式 API 逐 token 输出
- 当积累到一定字数（如 20-30 字）触发 TTS 合成
- TTS 使用流式 API 边生成边播放

**优势**：用户感知延迟从 2-5s 降至 500ms 以内

**复杂度**：高，需要改造 LLM 调用、TTS 调用、WebSocket 推送全链路

---

### 2.2 LLM 响应优化

#### 2.2.1 系统提示词优化

**当前问题**：
- 提示词过长（Live2D 版本 ~500 字）
- 包含大量规则说明，占用 token
- 对回复长度和格式约束不足

**优化方案**：

```
## 角色设定（精简版）
你是虚拟主播「爱莉希雅」，活泼可爱的二次元少女。

## 输出规则（强约束）
1. 回复必须简洁，不超过 100 字
2. 禁止使用 Markdown 符号（#、*、-、1.）
3. 禁止使用列表、表格等格式化内容
4. 禁止输出思考过程、推理步骤
5. 使用口语化表达，结尾用「~」「呢」「呀」等语气词
6. 自然融入直播互动话术（如「感谢关注~」「欢迎新来的朋友~」）

## 格式要求（Live2D 模式）
格式：【语气】内容|目光方向|嘴巴状态
语气：开心/好奇/调皮/温柔/惊讶
目光：center/left/right/up/down
嘴巴：open/close
```

**预估 token 节省**：~60%（从 ~500 token 降至 ~200 token）

#### 2.2.2 输出长度硬约束

```python
# 在 _llm_process_node 中添加
MAX_RESPONSE_LENGTH = 100  # 最大回复字数

# LLM 返回后截断
response = llm_result.get("response", "")
if len(response) > MAX_RESPONSE_LENGTH:
    response = response[:MAX_RESPONSE_LENGTH] + "~"
    logger.info(f"回复超长，已截断至 {MAX_RESPONSE_LENGTH} 字")
```

#### 2.2.3 思考过程过滤

```python
# 在 chat_model.py 中
# 当只有 reasoning_content 时返回空
if hasattr(msg, "reasoning_content") and msg.reasoning_content and not msg.content:
    logger.debug(f"[LLM] 仅返回思考过程，丢弃")
    ai_resp = ""
# 当有 content 时忽略 reasoning_content
elif hasattr(msg, "content") and msg.content:
    ai_resp = msg.content
```

---

### 2.3 Agent 工具调用优化

#### 2.3.1 减少工具调用轮次

**当前**：最多 3 轮工具调用 + 1 轮最终生成 = 最多 4 次 LLM 调用

**优化方案**：
1. **智能判断**：在 Prompt 中明确"能直接回答就不调用工具"
2. **预检索**：RAG 检索结果直接注入上下文，减少搜索需求
3. **合并搜索**：多个搜索关键词合并为一次调用
4. **超时降级**：工具调用超时后直接基于已有信息回答

```python
MAX_TOOL_ROUNDS = 1  # 从 3 降至 1

# 仅在以下情况允许搜索：
# 1. 用户明确询问实时信息
# 2. 置信度低于阈值
# 3. 关键词匹配预设的搜索触发词
SEARCH_TRIGGERS = ["今天", "最新", "最近", "现在", "实时", "当前"]
```

#### 2.3.2 工具结果摘要

**当前**：完整搜索结果注入上下文，浪费 token

**优化**：
```python
# 搜索结果预摘要
def summarize_search_results(results, max_tokens=200):
    """将搜索结果摘要为固定长度"""
    summary = ""
    for r in results[:3]:
        summary += f"- {r['title']}: {r['snippet'][:80]}\n"
    return summary[:max_tokens]
```

---

### 2.4 缓存与复用

#### 2.4.1 语义缓存

**思路**：对相似问题复用之前的回答

```python
class SemanticCache:
    def __init__(self, threshold=0.9):
        self.threshold = threshold
        self.cache = {}  # hash -> (embedding, response)
    
    async def get(self, query):
        """查找缓存命中"""
        query_embedding = await self._embed(query)
        for cached_query, (emb, response) in self.cache.items():
            similarity = cosine_similarity(query_embedding, emb)
            if similarity >= self.threshold:
                logger.info(f"缓存命中: {cached_query} -> {response[:50]}")
                return response
        return None
    
    async def put(self, query, response):
        """存入缓存"""
        self.cache[query] = (await self._embed(query), response)
```

**适用场景**：
- 重复性问题（"你好"、"在吗"、"自我介绍"）
- 高频话题（游戏、动漫相关）

#### 2.4.2 RAG 结果缓存

```python
# 对相同 query_text 缓存检索结果
rag_cache = {}  # query_hash -> documents

def cached_rag_retrieval(query, force_refresh=False):
    query_hash = hash(query)
    if not force_refresh and query_hash in rag_cache:
        return rag_cache[query_hash]
    result = actual_rag_retrieval(query)
    rag_cache[query_hash] = result
    return result
```

---

### 2.5 流式响应

#### 2.5.1 LLM 流式调用

```python
async def streaming_chat(state):
    """流式 LLM 调用"""
    accumulated_text = ""
    chunk_buffer = ""
    
    async for chunk in llm.astream(chat_messages, stream=True):
        token = chunk.choices[0].delta.content
        accumulated_text += token
        chunk_buffer += token
        
        # 当积累到标点符号时触发 TTS
        if re.search(r'[。！？~!?,.\n]', chunk_buffer):
            await trigger_tts_chunk(chunk_buffer)
            chunk_buffer = ""
    
    # 处理剩余文本
    if chunk_buffer:
        await trigger_tts_chunk(chunk_buffer)
    
    return accumulated_text
```

#### 2.5.2 WebSocket 流式推送

```python
async def stream_to_client(chunks):
    """逐块推送给 Web UI"""
    for chunk in chunks:
        await ws_manager.broadcast({
            "type": "stream_delta",
            "content": chunk
        })
        await asyncio.sleep(0.02)  # 控制推送速率
```

---

### 2.6 上下文管理优化

#### 2.6.1 历史消息压缩

**当前**：context_control 节点处理全部历史消息

**优化**：
```python
def smart_history_compression(messages, max_turns=5):
    """智能历史压缩"""
    # 保留最近 N 轮对话
    recent = messages[-max_turns*2:]  # user + assistant 各一条
    
    # 更早的对话摘要为一条系统消息
    if len(messages) > max_turns * 2:
        older = messages[:-max_turns*2]
        summary = summarize_as_single_message(older)
        return [summary] + recent
    
    return messages
```

#### 2.6.2 动态 Token 分配

```python
def dynamic_token_allocation(system_prompt, history, rag_context, query):
    """动态分配 token 预算"""
    total_budget = 4096
    
    # 固定开销
    system_tokens = count_tokens(system_prompt)
    query_tokens = count_tokens(query)
    
    # 动态分配
    remaining = total_budget - system_tokens - query_tokens
    history_tokens = min(remaining * 0.5, count_tokens(history))
    rag_tokens = min(remaining * 0.3, count_tokens(rag_context))
    
    # 输出预留
    output_reserve = 512
    
    return {
        "system": system_tokens,
        "history": history_tokens,
        "rag": rag_tokens,
        "query": query_tokens,
        "output_reserve": output_reserve
    }
```

---

## 三、实施计划

### 阶段一：快速优化（1-2天）

| 优先级 | 优化项 | 预估效果 | 实施难度 |
|--------|--------|---------|---------|
| P0 | 精简系统提示词 | token 减少 60%，LLM 响应加快 | 低 |
| P0 | 回复长度硬约束 | 避免冗长回复 | 低 |
| P0 | 思考过程过滤 | 消除 reasoning_content 泄漏 | 低 |
| P1 | 减少工具调用轮次 (3→1) | 减少 0-6s 延迟 | 低 |
| P1 | 后处理并行化 | 首字响应提前 1-2s | 中 |

### 阶段二：中等优化（3-5天）

| 优先级 | 优化项 | 预估效果 | 实施难度 |
|--------|--------|---------|---------|
| P1 | 语义缓存 | 常见问题 0 延迟响应 | 中 |
| P2 | RAG 结果缓存 | 重复检索 0 延迟 | 低 |
| P2 | 搜索结果摘要 | 减少 50% 上下文 token | 低 |
| P2 | 智能历史压缩 | 减少历史消息占用 | 中 |

### 阶段三：进阶优化（1-2周）

| 优先级 | 优化项 | 预估效果 | 实施难度 |
|--------|--------|---------|---------|
| P2 | LLM 流式输出 | 用户感知延迟降至 500ms | 高 |
| P3 | TTS 流式合成 | 语音输出提前 1-2s | 高 |
| P3 | 流水线并行 | 整体体验大幅提升 | 高 |

---

## 四、关键指标

### 4.1 性能指标

| 指标 | 当前 | 阶段一目标 | 阶段二目标 | 阶段三目标 |
|------|------|-----------|-----------|-----------|
| 首字响应时间 | 3-5s | 1-2s | 0.5-1s | <500ms |
| 完整响应时间 | 5-10s | 3-5s | 2-3s | 1-2s |
| Token 消耗 | 4000+ | 2000-3000 | 1500-2000 | 1000-1500 |
| 工具调用率 | ~30% | ~10% | ~5% | ~3% |

### 4.2 质量指标

| 指标 | 目标 |
|------|------|
| 回复长度 | ≤ 100 字（不含动作格式） |
| 格式合规率 | ≥ 95%（无 Markdown 符号） |
| 思考泄漏率 | 0% |
| 缓存命中率 | ≥ 30%（语义缓存） |

---

## 五、风险评估

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 回复过短 | 信息不完整 | 设置最小长度阈值，允许追问补充 |
| 缓存过期 | 信息过时 | 设置 TTL，实时信息强制刷新 |
| 并行化冲突 | 状态不一致 | 使用锁和事务确保状态一致性 |
| 流式丢包 | 音频断续 | 实现重传机制和缓冲策略 |
| 工具调用减少 | 回答质量下降 | 置信度阈值动态调整 |

---

## 六、总结

### 6.1 投入产出比

- **阶段一**：以最小改动获得最大收益（首字响应时间降低 50-60%）
- **阶段二**：通过缓存和压缩进一步优化（成本节省 30-50%）
- **阶段三**：实现流式体验，大幅提升用户感知

### 6.2 建议实施顺序

1. 先做 P0 级优化（提示词、长度约束、思考过滤）—— 当天见效
2. 再做 P1 级优化（并行化、减少工具调用）—— 1-2 天
3. 最后考虑流式架构重构 —— 视需求优先级决定

### 6.3 核心原则

- **先快后稳**：优先缩短首字响应时间，再优化整体质量
- **渐进改造**：每个阶段独立验证，避免大改导致回归
- **数据驱动**：建立性能监控，用数据指导优化方向
