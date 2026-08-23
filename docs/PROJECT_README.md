# LLM_vtuber 项目说明书

## 1. 项目概述

### 1.1 项目定位

LLM_vtuber 是一个基于 LangGraph 框架的 AI 虚拟主播（VTuber）系统。它能够监听哔哩哔哩直播间弹幕，通过大语言模型（豆包 Doubao）生成智能回复，并支持 Live2D 虚拟形象动作控制和 TTS 语音合成，打造沉浸式的 AI 虚拟主播体验。

### 1.2 核心功能

| 功能 | 描述 | 可选 |
|------|------|------|
| 弹幕监听 | 监听指定哔哩哔哩直播间弹幕 | 是 |
| AI 对话 | 基于 RAG 的上下文感知智能回复 | 必须 |
| Live2D 控制 | 虚拟形象目光跟随、表情动作 | 是 |
| TTS 语音合成 | 将回复转为语音播放 | 是 |
| 浏览器工具 | LLM 可调用浏览器搜索/抓取网页 | 是 |
| 视觉分析 | 识别桌面/窗口画面内容 | 是 |

### 1.3 技术栈

- **核心框架**: LangGraph（状态机图）
- **大模型**: 豆包 Doubao API（`doubao-seed-1-8-251228`）
- **向量数据库**: Milvus（RAG 检索）
- **Live2D**: VTube Studio WebSocket API
- **TTS**: GPT-SoVITS 语音合成
- **弹幕**: biliDm（哔哩哔哩弹幕协议）
- **浏览器**: Playwright（Chromium/Edge）
- **视觉**: pygetwindow + 豆包多模态模型

### 1.4 项目结构

```
LLM_vtuber/
├── main.py                    # 主程序入口
├── config.ini                 # 全局配置文件
├── LLM/                       # LLM 节点模块
│   ├── LLM_node.py           # LLM 对话节点
│   ├── chat_model.py         # 聊天模型调用
│   └── data_persistence.py   # 数据持久化
├── RAG/                       # RAG 检索模块
│   ├── RAG_node.py           # RAG 节点
│   ├── Millvus_base.py       # Milvus 基础操作
│   └── code_create_collection.py
├── live2d/                    # Live2D 控制模块
│   ├── live2d_main.py        # Live2D 主控制
│   ├── live2d_controller_manager.py
│   └── diag_params.py
├── audio/                     # TTS 语音模块
│   ├── audio_main.py         # TTS 节点
│   └── api.py                # TTS API
├── broadcast/                 # 弹幕监听模块
│   ├── bili_main.py          # 哔哩哔哩监听主程序
│   ├── config.json           # 弹幕配置
│   └── blivedm/              # 弹幕协议库
├── tool/                      # 工具系统
│   ├── tool_node.py          # 工具注册与调度
│   ├── browser_tool.py       # 浏览器工具
│   └── vision_tool.py        # 视觉分析工具
├── prompt/                    # 提示词配置
│   ├── character.json        # 角色设定
│   └── prompt_load.py        # 提示词加载
├── docs/                      # 文档目录
│   └── PROJECT_README.md     # 本说明书
└── memory_node.py             # 记忆节点
```

---

## 2. 架构设计

### 2.1 LangGraph 执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        LLMState                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ START → init → rag_retrieval → llm_process → rag_save          │
└─────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
┌─────────────────────┐               ┌─────────────────────┐
│  Live2D 已启用?     │               │  Live2D 未启用     │
└─────────────────────┘               └─────────────────────┘
            │ 是                                │
            ▼                                   │
┌─────────────────────┐                         │
│     live2d 节点     │                         │
└─────────────────────┘                         │
            │                                   │
            ▼                                   │
┌─────────────────────┐                         │
│  TTS 已启用?        │                         │
└─────────────────────┘                         │
            │ 是                否              │
            ▼                   ▼               ▼
┌─────────────────────┐  ┌─────────────────────┐
│      tts 节点       │  │     finalize 节点    │
└─────────────────────┘  └─────────────────────┘
            │
            ▼
┌─────────────────────┐
│     finalize 节点   │
└─────────────────────┘
```

### 2.2 节点说明

| 节点 | 功能 | 状态输入 | 状态输出 |
|------|------|----------|----------|
| `init` | 初始化默认字段 | LLMState | 设置 enable_live2d, system_prompt 等 |
| `rag_retrieval` | RAG 检索历史对话 | messages | context, retrieved_documents |
| `llm_process` | 调用大模型生成回复 | messages, context | response, tone, content, visual_focus |
| `rag_save` | 保存对话到 Milvus | messages, response | save_success |
| `live2d` | 执行 Live2D 动作 | response (解析后的动作) | live2d_status, live2d_message |
| `tts` | TTS 语音合成与播放 | tone, content | tts_played, tts_duration |
| `finalize` | 最终处理 | LLMState | LLMState |

### 2.3 消息处理队列

为避免 Live2D/TTS 并发冲突，系统使用异步队列串行处理消息：

```
弹幕 → HTTP POST → 消息队列 (Queue, maxsize=10)
                         │
                         ▼
                    队列 Worker (串行处理)
                         │
                         ▼
                    LangGraph 完整流程
```

---

## 3. 快速开始

### 3.1 环境要求

- Python 3.10+
- Conda 环境：`LLM`
- Windows 操作系统（Live2D/视觉功能）
- VTube Studio（Live2D 功能）
- GPT-SoVITS（TTS 功能，可选）

### 3.2 安装依赖

```bash
# 激活 conda 环境
conda activate LLM

# 核心依赖
pip install langgraph langchain langchain-core
pip install openai aiohttp
pip install pymilvus

# Live2D 依赖
pip install websockets

# 浏览器工具依赖
pip install playwright
playwright install chromium

# 视觉分析依赖
pip install pygetwindow pyrect Pillow

# TTS 依赖（可选）
pip install pyaudio wave

# 弹幕依赖
pip install bilibili-api-python
```

### 3.3 环境变量

```bash
# 豆包 API 配置
export Doubao_API_KEY="your-api-key"
export Doubao_API_URL="https://ark.cn-beijing.volces.com/api/v3"

# 哔哩哔哩 SESSDATA（可选）
export BILIBILI_SESSDATA="your-sessdata"
```

### 3.4 启动命令

```bash
# 基础模式（仅 AI 对话 + RAG）
python main.py

# 启用 Live2D
python main.py --live2d

# 启用 TTS
python main.py --tts

# 启用 Live2D + TTS
python main.py --live2d --tts

# 指定直播间
python main.py --live2d --room-id 904823

# 自定义 Live2D 参数
python main.py --live2d --live2d-speed 2.5 --live2d-sensitivity 1.0
```

### 3.5 命令行参数

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `--live2d` | flag | false | 启用 Live2D 功能 |
| `--live2d-host` | str | localhost | VTube Studio 地址 |
| `--live2d-port` | int | 8001 | VTube Studio 端口 |
| `--live2d-sensitivity` | float | 1.0 | 动作灵敏度 (0.1-2.0) |
| `--live2d-speed` | float | 2.5 | 响应速度/移动时间（秒） |
| `--live2d-smoothness` | float | 0.8 | 动作平滑度 (0.0-1.0) |
| `--live2d-eye-tracking` | flag | true | 启用目光追踪 |
| `--live2d-expression` | flag | true | 启用表情动作 |
| `--tts` | flag | false | 启用 TTS 功能 |
| `--room-id` | int | config.json | 指定直播间号 |

---

## 4. 模块说明

### 4.1 主程序 (main.py)

主程序负责：
1. 解析命令行参数
2. 初始化 HTTP 服务器（接收弹幕转发）
3. 启动哔哩哔哩弹幕监听子进程
4. 构建 LangGraph 图结构
5. 启动消息队列 Worker
6. 初始化浏览器工具
7. 初始化视觉分析工具

**HTTP 服务器接口**：
- 端口：8081（默认）
- 方法：POST
- 路径：`/`
- 请求体：`{"messages": [{"role": "user", "content": "...", "name": "..."}]}`
- 响应：`{"status": "queued"}`

### 4.2 LLM 节点

**LLM_node.py** 提供两个核心函数：

| 函数 | 描述 |
|------|------|
| `llm_chat_node(state)` | 纯聊天模式，无 RAG 上下文 |
| `context_aware_qa_node(state)` | 上下文感知问答，带 RAG 检索结果 |

**chat_model.py** 负责：
- 构建 OpenAI 兼容的 messages
- 调用豆包 API
- 管理对话历史截断
- 处理 function calling（浏览器/视觉工具）

**响应格式**：
```
【语气】回答内容|目光方向|嘴巴状态
```

| 字段 | 可选值 |
|------|--------|
| 语气 | 扮演慌张、调皮、尴尬、感动、积极、急了、假装、惊喜、开心、撩拨、难过、普通、撒娇、生气、严肃、疑问、自言 |
| 目光方向 | center, up, down, left, right, upleft, upright, downleft, downright |
| 嘴巴状态 | open（说话中）, close（思考/停止） |

### 4.3 RAG 检索

**RAG_node.py** 提供：

| 函数 | 描述 |
|------|------|
| `rag_retrieval_node(state)` | 从 Milvus 检索相关历史对话 |
| `rag_save_node(state)` | 将当前对话保存到 Milvus |

**Millvus_base.py** 管理：
- Milvus 连接（通过 `MilvusConnectionManager` 单例）
- Collection 创建与索引
- 向量插入与查询

**默认配置**：
- 数据库：`LLM_vtuber`
- Collection：`chat_history`
- 向量维度：1024
- 相似度度量：COSINE
- 检索 Top-K：3

### 4.4 Live2D 控制

**live2d/ 模块**：

| 文件 | 描述 |
|------|------|
| `live2d_main.py` | 核心控制：参数映射、晃动算法、移动执行 |
| `live2d_controller_manager.py` | WebSocket 连接管理、命令调度 |
| `diag_params.py` | 参数诊断工具 |

**核心算法**：
1. **参数映射**：将 Live2D 参数映射到 -1~1 或 0~1 范围
2. **常态晃动**：基于正弦函数的持续微幅抖动（±0.15°）
3. **移动执行**：从核心参数过渡到目标参数，叠加晃动效果
4. **方向模板**：9 个预设方向（上/下/左/右/4 对角/中）

**连接管理**：
- 使用 `asyncio.Lock` 防止并发 WebSocket 访问
- 移动时长默认 2.5 秒（可通过 `--live2d-speed` 调整）
- 目光方向分布：center ≤ 30%，其他方向 ≥ 70%

### 4.5 TTS 语音合成

**audio/audio_main.py** 提供 `tts_node`：
- 将 `tone` 和 `content` 合成为语音
- 通过 GPT-SoVITS API 调用
- 播放音频并返回时长

### 4.6 哔哩哔哩监听

**broadcast/bili_main.py** 特性：
- 自动读取 `config.json` 的房间号和输出端口
- 连接断开自动指数退避重连（1s→2s→4s→8s→max 30s）
- 弹幕/礼物/SC 去重（复合键：`room_id|type|uid|ts_int|content[:40]`）
- HTTP POST 转发到主程序（`http://127.0.0.1:<port>/`）

**config.json 配置**：
```json
{
  "ROOM_IDS": "904823",
  "output_port": 8081
}
```

### 4.7 工具系统

**tool/tool_node.py** 提供：
- `ToolRegistry`：工具注册表
- `tool_dispatch_node`：工具调度节点
- 已注册工具：

| 工具名 | 功能 | 来源 |
|--------|------|------|
| `web_search` | 搜索引擎搜索 | browser_tool.py |
| `fetch_webpage` | 抓取网页内容 | browser_tool.py |
| `capture_window` | 窗口/全屏截图 | vision_tool.py |
| `vision_analyze` | 视觉分析截图内容 | vision_tool.py |
| `calculator` | 数学计算 | tool_node.py |
| `play_audio` | 播放音频 | tool_node.py |

### 4.8 视觉分析（新增）

**tool/vision_tool.py** 提供：

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `capture_window` | 截取指定窗口或全屏 | `window_title`: 窗口标题关键词（可选） |
| `vision_analyze` | 视觉分析桌面/窗口内容 | `target`: "desktop" 或 "窗口:标题" |

**核心流程**：
1. 使用 `pygetwindow` 获取窗口列表并截图
2. 将截图转为 base64
3. 调用豆包多模态模型（`doubao-vision-pro`）分析
4. 返回自然语言描述

---

## 5. 配置说明

### 5.1 config.ini

#### [bilibili] - 弹幕配置

```ini
room_id = 904823              # 直播间号
sessdata = ...                # SESSDATA cookie（可选）
danmaku_duration = 10         # 弹幕获取持续时间（秒）
danmaku_capture_interval = 0.1 # 弹幕捕获间隔（秒）
danmaku_batch_size = 10       # 弹幕批处理大小
danmaku_max_queue_size = 1000 # 弹幕队列最大大小
```

#### [live2d] - Live2D 配置

```ini
enabled = true
host = localhost
port = 8001
http_port = 8888
```

#### [browser] - 浏览器工具配置

```ini
enabled = true
headless = true               # 无头模式
timeout = 30                  # 操作超时（秒）
search_engine = bing          # 搜索引擎
max_content_length = 5000     # 抓取内容最大长度
tool_timeout = 60             # 工具调用超时
```

#### [vision] - 视觉分析配置

```ini
enabled = true
default_window =              # 默认窗口（留空=全屏）
screenshot_dir = ./screenshots
model = doubao-vision-pro-32k
api_url = 
```

#### [model] - 模型配置

```ini
model = doubao-seed-1-8-251228
temperature = 0.7
max_tokens = 1000
```

#### [connection] / [api] - 连接与 API

```ini
[connection]
timeout = 30
reconnect_attempts = 5
reconnect_interval = 5

[api]
rate_limit = 10
retry_attempts = 3
retry_interval = 1.0
```

### 5.2 broadcast/config.json

```json
{
  "ROOM_IDS": "904823",
  "output_port": 8081,
  "DANMAKU_TYPES": ["DANMAKU", "GIFT", "SC"]
}
```

---

## 6. API 接口

### 6.1 HTTP 服务器

**POST** `/`

接收弹幕消息并放入处理队列。

**请求体**：
```json
{
  "messages": [
    {
      "role": "user",
      "content": "用户 XXX 说: 主播好厉害！",
      "name": "用户名"
    }
  ]
}
```

**响应**：
```json
{
  "status": "queued"
}
```

### 6.2 Live2D WebSocket

**连接**：`ws://localhost:8001`

**主要命令**：
- `LoadModelData`：加载模型
- `GetParameters`：获取参数
- `SetParameters`：设置参数
- `GetExpressionList`：获取表情列表
- `ActivateExpression`：激活表情

### 6.3 TTS API

**端点**：GPT-SoVITS HTTP API

**请求**：
```python
POST /tts
{
  "text": "要合成的文本",
  "character": "角色名"
}
```

---

## 7. 扩展指南

### 7.1 添加新工具

1. 在 `tool/` 下创建新模块（如 `custom_tool.py`）
2. 实现工具函数，返回 `Dict[str, Any]`
3. 在 `tool/tool_node.py` 中注册：
```python
from tool.custom_tool import my_tool
tool_registry.register_tool("my_tool", my_tool)
```
4. 将工具 Schema 添加到 LLM 的 function calling 列表

### 7.2 添加新节点

1. 在 `LangGraphManager.build_graph()` 中添加节点：
```python
self.graph.add_node("my_node", self._my_node)
```
2. 添加边连接到图结构
3. 实现节点函数，接收 `LLMState` 返回 `LLMState`

### 7.3 添加新的 RAG Collection

```python
from RAG.Millvus_base import MilvusConnectionManager
manager = MilvusConnectionManager()
manager.create_collection(
    collection_name="new_collection",
    vector_dim=1024,
    description="新集合描述"
)
```

### 7.4 自定义系统提示词

修改 `prompt/character.json` 或 `main.py` 中的 `SYSTEM_PROMPT_LIVE2D` 常量。

---

## 8. 故障排除

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| Live2D 连接失败 | VTube Studio 未启动 | 启动 VTube Studio 并启用 API |
| TTS 不播放 | GPT-SoVITS 未运行 | 启动 GPT-SoVITS 服务 |
| 弹幕不显示 | SESSDATA 过期 | 重新获取 SESSDATA |
| Milvus 连接失败 | Milvus 未启动 | 启动 Milvus 服务 |
| 视觉工具报错 | pygetwindow 未安装 | `pip install pygetwindow` |
| API 调用超时 | 网络问题 | 检查网络连接，增加超时时间 |

---

## 9. 安全注意事项

1. **API Key 安全**：使用环境变量存储密钥，不要硬编码
2. **屏幕截图隐私**：视觉功能可能截到敏感信息，请谨慎使用
3. **弹幕安全**：不要在公开场合暴露 SESSDATA
4. **URL 安全**：浏览器工具已屏蔽内网地址，防止 SSRF 攻击

---

## 附录

### A. 语气词汇表

| 语气 | 适用场景 |
|------|----------|
| 普通 | 日常对话、中性回答 |
| 开心 | 收到夸奖、好消息 |
| 调皮 | 开玩笑、调侃 |
| 撒娇 | 卖萌、请求 |
| 生气 | 不满、抗议 |
| 难过 | 同情、遗憾 |
| 疑问 | 询问、好奇 |
| 严肃 | 重要话题、说明 |
| 惊喜 | 意外之喜 |
| 尴尬 | 冷场、被戳穿 |
| 感动 | 温情时刻 |
| 撩拨 | 挑逗、吸引力 |
| 急了 | 催促、紧急 |
| 扮演慌张 | 假装害怕 |
| 假装 | 假装行为 |
| 积极 | 鼓励、支持 |
| 自言 | 自言自语 |

### B. 目光方向分布图

建议分布：
- **center**：≤ 30%（思考、中性）
- **up/upleft/upright**：≥ 20%（思考、惊喜、开心）
- **down/downleft/downright**：≥ 15%（难过、害羞、严肃）
- **left/right**：≥ 20%（调皮、撒娇、观察）