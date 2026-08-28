# LLM_vtuber 项目说明书

## 1. 项目概述

### 1.1 项目定位

LLM_vtuber 是一个基于 LangGraph 框架的 AI 虚拟主播（VTuber）系统。它监听哔哩哔哩直播间弹幕，通过豆包大模型生成智能回复，并支持 Live2D 虚拟形象动作控制和 TTS 语音合成，打造沉浸式 AI 虚拟主播体验。

### 1.2 核心功能

| 功能 | 描述 | 可选 |
|------|------|------|
| 弹幕监听 | B 站 WebSocket 实时弹幕，断线指数退避重连 | 是 |
| AI 对话 | 豆包 Doubao 大模型，多轮上下文感知，意图预判 + function calling | 必须 |
| RAG 知识库 | Milvus 向量检索 + 语义缓存 + 检索短路 | 可选 |
| Live2D 控制 | VTS 参数注入，常态呼吸/漂移，目光跟随 + 表情系统（语气自动推断） | 是 |
| TTS 语音 | 本地 GPT-SoVITS / 云端火山引擎，HTTP 单向流式 + JSON 流解析 | 是 |
| 浏览器工具 | Playwright 驱动，自动搜索 + 语义搜索缓存 | 是 |
| 视觉分析 | 窗口截图 + 豆包多模态，让 LLM「看见」桌面 | 是 |
| WebUI 仪表盘 | FastAPI + WebSocket 实时日志、状态监控、远程开关 | 内置 |

### 1.3 技术栈

- **核心框架**: LangGraph（状态机图）
- **大模型**: 豆包 Doubao API
- **向量数据库**: Milvus（RAG 检索）
- **Live2D**: VTube Studio WebSocket API
- **TTS**: GPT-SoVITS / 火山引擎豆包语音 V3
- **弹幕**: biliDm（项目自带，无需额外 pip）
- **浏览器**: Playwright（Chromium）
- **视觉**: pygetwindow + 豆包多模态模型
- **WebUI**: FastAPI + uvicorn + WebSocket

### 1.4 项目结构

```
LLM_vtuber/
├── main.py                       # 主入口：LangGraph 构建 + FastAPI + 消息队列 Worker
├── config.ini                    # 全局配置（非敏感运行时参数）
├── .env.example                  # 环境变量模板（复制为 .env 后填写密钥）
├── requirements.txt              # Conda 完整导出（建议手动装核心依赖）
├── memory_manager.py             # 记忆管理
│
├── LLM/                          # 🧠 LLM 对话核心
│   ├── LLM_node.py               # LangGraph 节点 + 意图分类器 + 工具轮次动态规划
│   ├── chat_model.py             # 豆包 API 调用 + function calling + reasoning_content 过滤
│   ├── live2d_models.py          # Live2DResponse Schema + TONE_TO_EXPRESSION 映射表
│   └── data_persistence.py       # 对话历史持久化
│
├── RAG/                          # 📚 检索增强生成
│   ├── RAG_node.py               # LangGraph RAG 节点（含检索短路逻辑）
│   ├── Millvus_base.py           # MilvusConnectionManager（单例 + 连接池）
│   ├── knowledge_base.py         # MinIO 文件 + Milvus 向量双存储
│   ├── context_controller.py     # 上下文窗口管理 + 摘要器
│   └── rag_retrieval_graph.py    # RAG 检索子图
│
├── live2d/                       # 🎭 Live2D 虚拟形象
│   ├── live2d_main.py            # 核心控制：idle_movement + set_expression + dump_vts_current_params
│   ├── vtuber_studio_info.py     # VTube Studio WebSocket API 封装（auth + send_request + get_tracking_parameters）
│   ├── live2d_controller_manager.py  # WebSocket 连接管理、命令调度
│   └── live2d_base_centrol.py    # 旧版状态机控制（已弃用，保留参考）
│
├── audio/                        # 🔊 TTS 语音合成
│   ├── audio_main.py             # TTS LangGraph 节点（检查 enable_tts / enable_cloud_tts）
│   ├── cloud_tts.py              # 火山引擎 TTS V3 HTTP 单向流式 + JSON 流解析
│   ├── api.py                    # GPT-SoVITS HTTP API 客户端
│   ├── api_v2.py                 # TTS API V2 版本（备用）
│   ├── audio_deal.py             # 音频处理工具
│   └── tts_protocols.py          # TTS 协议抽象
│
├── broadcast/                    # 🎤 B 站弹幕监听
│   ├── bili_main.py              # 弹幕主进程（自动重载 blivedm）
│   ├── bilibili_state.py         # BilibiliCookie + 心跳管理（读取 $BILIBILI_SESSDATA）
│   ├── config.json               # {ROOM_IDS, output_port, SESSDATA}
│   └── blivedm/                  # 弹幕协议库（项目自带）
│
├── tool/                         # 🛠️ 工具系统
│   ├── tool_node.py              # LangGraph 工具调度节点 + ToolRegistry
│   ├── browser_tool.py           # Playwright 浏览器 + SemanticSearchCache（BERT 相似度缓存）
│   └── vision_tool.py            # pygetwindow 截图 + 豆包视觉模型分析
│
├── webui/                        # 📊 WebUI 仪表盘
│   ├── app.py                    # FastAPI + WebSocket 后端（/ws/logs, /ws/status）
│   ├── api.py                    # REST API 路由（/api/send, /api/status, /api/logs, /api/control）
│   ├── state.py                  # 运行时状态 + 日志缓冲区
│   └── index.html                # 前端单页应用
│
├── prompt/                       # 📝 角色配置
│   ├── character.json            # 角色人设 + 系统提示词模板
│   └── prompt_load.py            # 提示词加载器
│
├── danmu_tts/                    # 🔁 AI 读弹幕 TTS（可选功能）
├── vision_danmu/                 # 👁️ 视觉弹幕（可选功能）
├── knowledge_base/               # 📖 知识库原始 .txt 数据（不入库）
├── script/                       # 🧪 辅助脚本
└── docs/                         # 📄 详细技术文档
```

---

## 2. 架构设计

### 2.1 LangGraph 执行流程（并行化改造后）

```
┌─────────────────────────────────────────────────────────────────┐
│ START → init → load_memory → rag_retrieval → context_control   │
│                                          ↓                       │
│                              llm_process (意图预判 + tool call)  │
│                                          ↓                       │
│                                    finalize (首字响应点)         │
│                                          ↓                       │
│   (异步并行后处理，不阻塞回复输出)                                    │
│   ├── rag_save       保存对话到 Milvus                          │
│   ├── save_memory    保存对话记忆                                │
│   ├── live2d_action  表情注入 + 目光移动（如启用）                │
│   └── tts / cloud_tts  语音合成（如启用）                         │
└─────────────────────────────────────────────────────────────────┘
```

**关键点**：`llm_process` 直接连 `finalize`，首字响应不再等待后处理。后处理通过 `asyncio.create_task` 异步并行执行。

### 2.2 节点说明

| 节点 | 功能 | 状态输入 | 状态输出 |
|------|------|----------|----------|
| `init` | 初始化默认字段、读取 config.ini 和命令行参数 | LLMState | enable_live2d, system_prompt, enable_tts 等 |
| `load_memory` | 加载历史对话记忆 | messages | loaded_memories |
| `rag_retrieval` | RAG 检索（含检索短路逻辑） | messages, loaded_memories | context, retrieved_documents |
| `context_control` | 上下文窗口管理 + 摘要 | messages, context | trimmed_messages |
| `llm_process` | 意图分类 + 调用大模型 + 动态工具轮次 | trimmed_messages | response, tone, content, visual_focus |
| `finalize` | 最终处理（首字响应点） | LLMState | 立即返回给用户 |
| _(并行)_ `rag_save` | 保存对话到 Milvus | messages, response | save_success |
| _(并行)_ `live2d_action` | 表情注入 + 目光移动 | response (tone) | live2d_status |
| _(并行)_ `tts` / `cloud_tts` | 语音合成与播放 | tone, content | tts_played |

### 2.3 消息处理队列

异步队列（`queue.Queue`，maxsize=10）串行处理，避免 Live2D/TTS 并发冲突：

```
弹幕 → bili_main.py (HTTP POST)
    → http://127.0.0.1:8081/ (POST {"messages": [...]})
    → main.py Queue Worker
    → LangGraph 完整流程
```

### 2.4 并行后处理机制

```python
# main.py — _trigger_parallel_post_process(state, config)
# 检查 enable_tts / enable_cloud_tts / enable_live2d
# 创建异步任务：
asyncio.create_task(_async_rag_save(state_snapshot, config))
asyncio.create_task(_async_save_memory(state_snapshot, config))
if self.enable_live2d:
    asyncio.create_task(_async_live2d_action(state_snapshot, config))
if self.enable_cloud_tts:
    asyncio.create_task(_async_cloud_tts(state_snapshot, config))
elif self.enable_tts:
    asyncio.create_task(_async_tts_synthesize(state_snapshot, config))
```

所有异步函数接收 `dict(state)` 快照，避免并发修改问题。

---

## 3. 快速开始

### 3.1 环境要求

- Python 3.10+（推荐 3.12）
- Conda 环境：推荐 `LLM`（或自定义）
- Windows 操作系统（Live2D / 视觉分析依赖 Win32 API）
- VTube Studio（Live2D 功能，需启用 API 插件）
- Milvus（RAG 功能，可 Docker 启动）

### 3.2 安装依赖

```bash
# Conda 环境创建
conda create -n LLM python=3.12 -y
conda activate LLM

# 核心依赖
pip install langgraph==1.0.9 langchain-core==1.2.16
pip install openai==2.24.0 httpx websockets==16.0
pip install pymilvus==2.6.9 fastapi==0.141.1 uvicorn==0.52.3 sse-starlette==3.4.8
pip install pydantic==2.13.4 numpy==1.26.4
pip install playwright==1.62.0
playwright install chromium
pip install pywin32==312 pillow python-dotenv==1.2.1

# 音频（可选）
pip install pyaudio==0.2.14 pydub==0.25.1 sounddevice==0.5.5
```

> 注意：`requirements.txt` 是 Conda 本地绝对路径导出，建议按以上手动安装。

### 3.3 环境变量

所有密钥通过环境变量注入，config.ini 中对应字段留空。复制 `.env.example` 为 `.env` 后填写：

```bash
# ============================================================
# 🔑 必须：豆包大模型
# ============================================================
Doubao_API_KEY=your-doubao-api-key-here
Doubao_API_URL=https://ark.cn-beijing.volces.com/api/v3

# ============================================================
# 🎤 可选：火山引擎云端 TTS
# 优先级（自动查找）：DOUBAO_API_KEY → VOLCENGINE_API_KEY → ARK_API_KEY → TTS_KEY
# ============================================================
VOLCENGINE_API_KEY=

# ============================================================
# 📺 可选：B 站 SESSDATA（提高弹幕接收等级至 100+）
# ============================================================
BILIBILI_SESSDATA=

# ============================================================
# 📚 可选：Milvus + MinIO（RAG 知识库）
# ============================================================
MILVUS_TOKEN=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_ENDPOINT=localhost:9000
MINIO_SECURE=false
KNOWLEDGE_BUCKET=knowledge-files
```

### 3.4 config.ini 关键配置

```ini
[bilibili]
room_id = 30655190
sessdata =                          ; 留空 → 用 $BILIBILI_SESSDATA

[live2d]
enabled = false                      ; 启动时 --live2d 覆盖
host = localhost
port = 8001
sensitivity = 1.0
response_speed = 2.5
smoothness = 0.8

[cloud_tts]
enabled = false                      ; 启动时 --cloud-tts 覆盖
api_version = v3
resource_id = seed-tts-2.0           ; 2.0大模型 / 1.0旧版
voice_type = zh_female_vv_uranus_bigtts
format = wav                         ; wav（含 RIFF 头，推荐）/ pcm（裸数据）
sample_rate = 24000
timeout = 30

[model]
model = doubao-seed-1-8-251228
temperature = 0.7
max_tokens = 1000

[milvus]
uri = http://localhost:19530
token =                              ; 留空 → 用 $MILVUS_TOKEN
database = LLM_vtuber

[minio]
endpoint = localhost:9000
access_key =                         ; 留空 → 用 $MINIO_ACCESS_KEY
secret_key =                         ; 留空 → 用 $MINIO_SECRET_KEY
secure = false
bucket = knowledge-files

[vision]
enabled = true
model = doubao-seed-1-6-vision-250815

[danmu_tts]
enabled = false
read_interval = 2

[vision_danmu]
enabled = false
capture_interval = 5
```

### 3.5 启动命令

```bash
# 最简（仅 AI 对话 + WebUI）
python main.py

# 一键全功能
python main.py --all

# 按需组合
python main.py --live2d --cloud-tts --room-id 30655190

# 指定直播间（覆盖 config.ini 和 config.json）
python main.py --room-id 30655190
```

启动成功后控制台会打印：
```
[LangGraph] 初始化完成，WebUI 运行在 http://127.0.0.1:8081
```

### 3.6 命令行参数

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `--all` | flag | - | 一键启用 Live2D + TTS + 弹幕监听 |
| `--live2d` | flag | false | 启用 Live2D 虚拟形象 |
| `--tts` | flag | false | 本地 TTS (GPT-SoVITS) |
| `--cloud-tts` | flag | false | 云端 TTS (火山引擎) |
| `--danmu-tts` | flag | false | AI 读弹幕 |
| `--vision-danmu` | flag | false | 视觉弹幕 |
| `--room-id` | int | config.ini | 指定 B 站直播间号（覆盖 config.ini 和 config.json） |
| `--live2d-host` | str | localhost | VTube Studio 地址 |
| `--live2d-port` | int | 8001 | VTube Studio 端口 |
| `--live2d-speed` | float | 2.5 | Live2D 动作过渡时长（秒） |
| `--live2d-sensitivity` | float | 1.0 | Live2D 动作灵敏度 (0.1-2.0) |
| `--live2d-smoothness` | float | 0.8 | Live2D 动作平滑度 (0.0-1.0) |

### 3.7 配置优先级

```
命令行参数 > config.ini > 环境变量 > 代码默认值
```

---

## 4. 模块说明

### 4.1 主程序 (main.py)

主程序负责：
1. 解析命令行参数，读取 config.ini，加载环境变量
2. 初始化 HTTP 服务器（FastAPI 接收弹幕转发 + WebUI）
3. 启动哔哩哔哩弹幕监听子进程（bili_main.py）
4. 构建 LangGraph 图结构（LangGraphManager）
5. 启动消息队列 Worker（asyncio 串行处理）
6. 初始化浏览器工具 + 视觉分析工具

**HTTP 接口（弹幕接收）**：
- 端口：8081（默认，同时也是 WebUI 端口）
- 方法：POST
- 路径：`/`
- 请求体：`{"messages": [{"role": "user", "content": "...", "name": "..."}]}`
- 响应：`{"status": "queued"}`

### 4.2 LLM 对话核心

#### LLM_node.py

| 组件 | 描述 |
|------|------|
| `llm_chat_node(state)` | LLM 对话节点（意图预判 + tool call） |
| `IntentClassifier` | 本地轻量意图分类器，四种意图：`skip` / `force_search` / `reduce_rounds` / `normal` |
| `summarize_search_results` | 将多条搜索结果压缩为 {title, snippet, url} 结构化文本 |

**意图分类器特性**：
- 基于规则 + 关键词的本地分类，零网络开销
- 准确率 100%（覆盖问候、实时查询、普通对话）
- 在 LLM 调用前预判是否需要 function calling，动态减少工具轮次
- 效果：减少 ~30% 无效搜索调用

#### chat_model.py

| 职责 | 说明 |
|------|------|
| API 调用 | 豆包 Doubao API（OpenAI 兼容格式） |
| reasoning 过滤 | 只取 `msg.content`，忽略 `msg.reasoning_content` |
| 对话历史 | 自动截断，控制 token 总量 |
| Function Calling | 浏览器搜索 / 视觉分析 / 计算器 |

#### live2d_models.py

```python
class Live2DResponse(BaseModel):
    tone: Literal[ALLOWED_TONES]       # 17 种固定语气
    content: str                        # 回答内容
    visual_focus: Direction | None      # 9 方向（center/up/down/...）
    mouth_state: Literal["open","close"] | None
    mouth_intensity: float | None       # 嘴巴开合度 0.0-1.0
    expression: Literal[...] | None      # 表情（开心/生气/难过/惊讶...）
    expression_duration: float           # 表情持续秒数
    hotkey: str | None                  # VTS 热键触发
```

**语气词 → 表情自动推断**：`TONE_TO_EXPRESSION` 映射表 + 模糊匹配 fallback

**响应格式**（当 Live2D 启用时）：
```
【语气】回答内容|目光方向|嘴巴状态
```

### 4.3 RAG 检索

#### RAG_node.py

| 组件 | 描述 |
|------|------|
| `rag_retrieval_node(state)` | RAG 检索（含短路逻辑） |
| `rag_save_node(state)` | 保存对话到 Milvus |
| `_should_skip_rag()` | 判断是否跳过检索（短消息/问候/纯表情 → skip） |

#### Millvus_base.py

- `MilvusConnectionManager` 单例管理连接
- key = `uri_token_db_name`，确保同配置复用
- 连接日志只显示新连接/重连，不显示重复使用

#### knowledge_base.py

- MinIO 文件存储 + Milvus 向量双存储
- 移除了 `minioadmin` / `root:Milvus` 等硬编码，全部走环境变量

**默认 Milvus 配置**：
- 数据库：`LLM_vtuber`
- Collection：`chat_history`
- 向量维度：1024
- 相似度：COSINE
- Top-K：3

### 4.4 Live2D 控制

#### 文件职责

| 文件 | 描述 |
|------|------|
| `live2d_main.py` | 核心控制：idle_movement + update_non_moving_state + set_expression + dump_vts_current_params |
| `vtuber_studio_info.py` | VTube Studio WebSocket API 封装（auth + send_request + get_tracking_parameters） |
| `live2d_controller_manager.py` | WebSocket 连接管理、命令调度 |

#### 核心原理：高频参数注入

VTube Studio 摄像头追踪以 ~20Hz 覆盖参数。我们以更高频注入（每 0.3s），
且必须设置 `faceFound=True` 让 VTS 接受我们的值。

**参数名必须用 `InputParameterListRequest`（人类可读格式）**，
不能用 `Live2DParameterListRequest`（返回 Param158 占位名）。

#### 常态循环（idle_movement）

```python
# 每 0.3s tick 一次
params = self.core_params.copy()    # 基础方向/表情值
params += breath_wave(now)          # ±4.8°, 0.15Hz (T≈6.67s)
params += drift_wave(now)           # ±2°,  0.33Hz (T≈3s)
await self.inject_parameter_data(params, face_found=True, mode="set")
```

呼吸和漂移用**多频正弦叠加**模拟自然不规则动作。

#### 目光移动

1. 设置 `self.core_params` 中的方向参数（`FaceAngleX=+12°` 表示向左看）
2. 由**下一个 idle tick** 自然叠加呼吸/漂移后注入
3. 过渡时长由 idle 循环频率决定（渐进式多帧过渡）

#### 表情系统（A-P0 已实现）

| 触发方式 | 说明 |
|----------|------|
| 显式指定 | LLM 返回 `expression="开心"` |
| 自动推断 | LLM 返回语气词 → `TONE_TO_EXPRESSION` 映射 |

表情通过 `EXPRESSION_PARAM_MAP` 映射为参数偏移：

```python
EXPRESSION_PARAM_MAP = {
    "开心": [("FaceAngleZ", +3), ("Brows", -0.3), ("EyeOpenLeft", +0.2)],
    "生气": [("Brows", +0.5), ("FaceAngleZ", -2), ("MouthOpen", -0.3)],
    ...
}
```

**表情与常态动作叠加**：表情参数只写入表情专属参数（Brows, EyeOpen*, MouthSmile*），
不覆盖方向/呼吸参数。idle 循环每次 tick 把 `core_params`（含表情）+ 呼吸 + 漂移一起注入。

#### 诊断快照（dump_vts_current_params）

每 200 tick（~60s）自动运行，对比 `_last_injected_values`（我们注入的期望值）vs VTS 实际值。

#### 操作锁

所有 WebSocket 操作必须持 `asyncio.Lock`（`operation_lock`），防止两条路径发送/接收交错导致的参数错乱。

#### 已知约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | VTS 模型加载延迟 | WebSocket 能连上但 modelLoaded=false，需等 3-5 秒 |
| 2 | 追踪抢回参数 | 注入频率 < 1s 会被摄像头追踪覆盖 |
| 3 | 两种参数名混淆 | `InputParameterListRequest` ≠ `Live2DParameterListRequest` |
| 4 | faceFound=False | 会被 VTS 拒绝，必须设 True |
| 5 | 语气词映射不全 | 未知语气词 → 表情推断失败 |

> 详细设计和架构见 [live2d_design.md](live2d_design.md)

### 4.5 TTS 语音合成

#### 本地 TTS（GPT-SoVITS）

- `audio_main.py → tts_node()`：HTTP API 调用
- 将 `tone` 和 `content` 合成为语音并播放
- **注意**：本地 TTS 和云端 TTS 互斥，只能启用一个

#### 云端 TTS（火山引擎豆包语音 V3）

| 项目 | 值 |
|------|------|
| 端点 | `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| 认证头 | `X-Api-Key: <volcengine-access-token>` |
| 响应格式 | JSON 流（每个对象含 `code` + `data[base64]`） |
| 解析方式 | `json.JSONDecoder.raw_decode` 逐流解析（不依赖换行符分割） |
| 支持格式 | `wav`（含 RIFF 头，推荐）/ `pcm`（裸 PCM 数据） |
| 默认音色 | `zh_female_vv_uranus_bigtts` |
| 默认采样率 | 24000Hz |

#### 两层开关逻辑

```
config.ini [cloud_tts] enabled + main.py self.enable_cloud_tts (命令行覆盖)
config.ini [tts] enabled + main.py self.enable_tts (命令行覆盖)
```

`_trigger_parallel_post_process` 和 `_async_tts_synthesize` 都检查这两层开关，
确保用户关闭后不会继续请求。

#### 已知坑

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 返回白噪音 | API 返回 MP3 但代码按 WAV 解析 | `config.ini format = wav`（推荐） |
| JSON 解析失败 | `data` 字段含换行符 | 已用 `JSONDecoder.raw_decode` 修复 |
| MP3 需转码 | 如需 MP3 格式输出，需 ffmpeg 转码 | 推荐使用 WAV 格式 |
| `reasoning_content` 泄漏 | 豆包 reasoning 字段传入 TTS | 已在 chat_model.py 过滤 |

### 4.6 哔哩哔哩监听

#### bili_main.py 特性

- 自动读取 `config.json` 的房间号和输出端口
- 连接断开指数退避重连（1s→2s→4s→8s→max 30s）
- 弹幕/礼物/SC 去重（复合键：`room_id|type|uid|ts_int|content[:40]`）
- HTTP POST 转发到主程序（`http://127.0.0.1:<port>/`）
- **停止监听时清空队列**（防止积压消息继续处理）

#### bilibili_state.py

- `BilibiliCookie`：读取 `$BILIBILI_SESSDATA` 环境变量
- 心跳管理：每 30s 发送一次心跳维持连接

#### config.json

```json
{
  "ROOM_IDS": "30655190",
  "SESSDATA": "",
  "output_port": 8081
}
```

### 4.7 工具系统

#### ToolRegistry（tool_node.py）

| 工具名 | 功能 | 来源 |
|--------|------|------|
| `web_search` | 搜索引擎搜索（带语义缓存） | browser_tool.py |
| `fetch_webpage` | 抓取网页内容 | browser_tool.py |
| `capture_window` | 窗口/全屏截图 | vision_tool.py |
| `vision_analyze` | 视觉分析截图内容 | vision_tool.py |
| `calculator` | 数学计算 | tool_node.py |
| `play_audio` | 播放音频 | tool_node.py |

#### SemanticSearchCache（browser_tool.py）

| 参数 | 值 |
|------|------|
| 缓存上限 | 50 条 |
| TTL | 1800s（30min） |
| 相似度阈值 | 0.88 |
| 实现方式 | Sentence-BERT 向量 + 余弦相似度 |

#### 视觉分析（vision_tool.py）

1. `pygetwindow` 获取窗口列表并截图
2. 截图转 base64
3. 调用豆包多模态模型分析
4. 返回自然语言描述

### 4.8 WebUI 仪表盘（webui/）

#### REST API

| 方法 | 路径 | 请求体 / 参数 | 说明 |
|------|------|------|------|
| POST | `/api/send` | `{"content": "你好"}` | 手动发送测试消息 |
| GET | `/api/status` | - | 完整运行状态（含 danmaku.enabled, live2d.online 等） |
| GET | `/api/logs` | `?limit=500&logger=Live2DMain` | 最近 N 条日志 |
| POST | `/api/control` | `{"action": "toggle_danmu"}` | 远程开关功能 |

#### WebSocket

| 路径 | 推送内容 |
|------|------|
| `/ws/logs` | 实时日志行（含 logger / level / message / timestamp） |
| `/ws/status` | 状态变更事件 |

#### 仪表盘状态同步

后端 `app.py` 的 `/api/status` 返回 `danmaku.enabled` 字段（基于 `_bili_process.poll()` 检查子进程状态），
前端 `index.html` 动态显示监听状态。

---

## 5. 扩展指南

### 5.1 添加新工具

```python
# 1. 在 tool/ 下创建模块（如 custom_tool.py）
def my_tool(args: dict) -> dict:
    return {"result": "..."}

# 2. 在 tool/tool_node.py 的 ToolRegistry 注册
tool_registry.register_tool("my_tool", my_tool)

# 3. 把工具 Schema 加到 LLM 的 function calling 列表
```

### 5.2 添加新 LangGraph 节点

```python
# LangGraphManager.build_graph() 中
self.graph.add_node("my_node", self._my_node)

# 节点函数接收 LLMState 返回 LLMState
def _my_node(self, state: LLMState) -> LLMState:
    ...
    return {**state, "field": value}
```

### 5.3 自定义系统提示词

修改 `prompt/character.json` 或 `main.py` 中的 `SYSTEM_PROMPT_LIVE2D` / `SYSTEM_PROMPT_DEFAULT`。

### 5.4 新增 RAG Collection

```python
from RAG.Millvus_base import MilvusConnectionManager
manager = MilvusConnectionManager()
manager.create_collection(
    collection_name="new_collection",
    vector_dim=1024,
    description="新集合描述"
)
```

---

## 6. 已知问题与故障排除

### Live2D

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| VTS modelLoaded=false | 模型加载有延迟 | 启动后等 3-5 秒再发弹幕 |
| 快照显示 Param158 而非 FaceAngleX | 用了错误的 API | 代码已修复，用 InputParameterListRequest |
| 注入被追踪抢回 | 注入频率 < 1s | idle 循环必须 ≥ 每 0.5s 注入 |
| 表情推断失败（expression=None） | 语气词不在映射里 | 已加模糊匹配 fallback；可扩展映射表 |
| 常态动作停止 | 表情参数覆盖了核心呼吸参数 | 表情只写入专属参数，不覆盖方向/呼吸 |

### TTS

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| 云端 TTS 返回白噪音 | API 返回 MP3 但按 WAV 解析 | `config.ini format = wav` |
| JSON 解析错误 | `data` 含换行符 | 已用 JSONDecoder.raw_decode 修复 |
| 关闭后仍请求 | 只检查模块可用性 | 已加 self.enable_tts / enable_cloud_tts 检查 |
| `reasoning_content` 泄漏 | 豆包 reasoning 字段传入 | chat_model.py 已过滤 |

### 弹幕

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| 停止后仍处理积压 | 停止时未清空队列 | 已加 queue.clear() |
| 仪表盘状态不同步 | 后端未返回 danmaku.enabled | 已补全 |
| 连接频繁断开 | SESSDATA 过期 | 重新获取 SESSDATA |

### 通用

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| `.gitignore` 排除核心文件 | 曾误添加 | 已移除 browser_tool.py 和 vtuber_studio_info.py |
| Milvus 连接失败 | 未启动 | 启动 Milvus（Docker） |
| 新环境装不上 requirements.txt | Conda 绝对路径导出 | 按"快速开始"手动装核心依赖 |
| 端口冲突（8081） | 残留进程 | 自动清理：查找并 kill 占用进程 |

---

## 7. Agent 效率优化

### 7.1 已实施的优化

| 编号 | 优化 | 效果 |
|------|------|------|
| P0-1 | 精简系统提示词 | Token 节省 ~60% |
| P0-2 | 回复长度硬约束（100 字） | 避免冗长回复 |
| P0-3 | 思考过程过滤 | reasoning_content 不再泄漏 |
| 2.1A | 后处理并行化 | 首字响应不再等待 TTS/存储 |
| — | 功能状态实例化 | 启停功能不重建图 |
| — | 停止时清空队列 | 停止后不再处理积压 |
| — | 仪表盘状态同步 | 弹幕/功能状态实时反映 |

### 7.2 RAG / 搜索优化

| 优化 | 机制 | 效果 |
|------|------|------|
| RAG 短路 | `_should_skip_rag()` 跳过短消息/问候 | 减少无效 Milvus 调用 |
| 语义搜索缓存 | Sentence-BERT 相似度 > 0.88 复用结果，TTL 30min | 减少 50% 重复搜索 |
| 搜索摘要 | 多条结果压缩为结构化文本 | 减少 LLM token |
| 意图预判 | 本地规则 + 关键词分类器（100% 准确率） | 动态减少工具轮次 |

### 7.3 首字响应时间对比

| 阶段 | 首字响应 | 完整响应 |
|------|----------|----------|
| 优化前 | 3-10s | 含 TTS 合成等待 |
| P0 后 | 1-2s | 后处理异步并行 |

---

## 附录

### A. 语气词汇表（Live2D tone 字段）

| 语气 | 适用场景 | 对应表情 |
|------|----------|----------|
| 普通 | 日常对话、中性回答 | None |
| 开心 | 收到夸奖、好消息 | smile |
| 调皮 | 开玩笑、调侃 | smile |
| 撒娇 | 卖萌、请求 | shy |
| 生气 | 不满、抗议 | angry |
| 难过 | 同情、遗憾 | sad |
| 疑问 | 询问、好奇 | None |
| 严肃 | 重要话题、说明 | None |
| 惊喜 | 意外之喜 | surprised |
| 尴尬 | 冷场、被戳穿 | shy |
| 感动 | 温情时刻 | sad |
| 撩拨 | 挑逗、吸引力 | wink |
| 急了 | 催促、紧急 | angry |
| 扮演慌张 | 假装害怕 | surprised |
| 假装 | 假装行为 | None |
| 积极 | 鼓励、支持 | smile |
| 自言 | 自言自语 | None |

### B. 目光方向分布图

建议分布（避免同方向重复微调导致参数累积偏移）：
- **center**：≤ 30%（思考、中性）
- **up/upleft/upright**：≥ 20%（思考、惊喜、开心）
- **down/downleft/downright**：≥ 15%（难过、害羞、严肃）
- **left/right**：≥ 20%（调皮、撒娇、观察）

### C. LangGraph 完整执行顺序

```
START
  ↓
init (初始化 enable_* 标志、system_prompt)
  ↓
load_memory (加载历史对话记忆)
  ↓
rag_retrieval (检索 + 短路判断)
  ↓
context_control (上下文窗口截断 + 摘要)
  ↓
llm_process (意图预判 → 动态工具轮次 → function calling)
  ↓
finalize (首字响应点 → 立即返回)
  ↓ (异步并行)
  ├── rag_save
  ├── save_memory
  ├── live2d_action (如启用)
  └── tts / cloud_tts (如启用)
```
