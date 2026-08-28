# LLM_VTuber 🎤🤖

> 基于 LangGraph + LLM 的 AI 虚拟主播系统，监听 B 站弹幕 → RAG 检索 → LLM 回复 → Live2D 动作 + TTS 语音，全链路自动完成。

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" />
  <img src="https://img.shields.io/badge/framework-LangGraph-orange" />
  <img src="https://img.shields.io/badge/LLM-豆包-Doubao-red" />
  <img src="https://img.shields.io/badge/Live2D-VTube%20Studio-pink" />
  <img src="https://img.shields.io/badge/RAG-Milvus-purple" />
</p>

---

## ✨ 功能亮点

| 模块 | 特性 |
|------|------|
| 🎯 **弹幕监听** | B 站 WebSocket 实时弹幕，断线指数退避重连，支持 DANMAKU / GIFT / SC |
| 🧠 **AI 对话** | 豆包 Doubao 大模型，多轮上下文感知，语气/情绪自动推断 + 意图预判 |
| 📚 **RAG 知识库** | Milvus 向量检索，历史对话记忆，语义缓存 + 检索短路双优化 |
| 🎭 **Live2D 控制** | VTube Studio WebSocket 参数注入，呼吸/漂移常态循环，目光跟随 + 表情系统（语气自动推断） |
| 🔊 **TTS 语音** | 本地 GPT-SoVITS / 云端火山引擎，HTTP 单向流式 + JSON 流解析 |
| 🌐 **浏览器工具** | Playwright 驱动，自动搜索 + 网页抓取，语义搜索缓存复用 |
| 🖥️ **视觉分析** | 窗口截图 + 豆包多模态（doubao-seed-1-6-vision-250815），让 LLM「看见」桌面 |
| 📊 **WebUI 仪表盘** | FastAPI + WebSocket 实时日志、状态监控、远程开关控制 |

---

## 🏗️ 架构概览

```
                  ┌─────────────────────────────────────┐
                  │       Bilibili Live Room            │
                  │     (弹幕 / 礼物 / SuperChat)        │
                  └──────────────┬──────────────────────┘
                                 │ WebSocket (biliDm)
                                 ▼
                  ┌─────────────────────────────────────┐
                  │   消息队列 Queue (maxsize=10)        │
                  │   串行 Worker 避免并发冲突            │
                  └──────────────┬──────────────────────┘
                                 │
                                 ▼
    ┌──────────────────────────────────────────────────────────┐
    │                    LangGraph 状态机                      │
    │                                                          │
    │  init → load_memory → rag_retrieval → context_control →  │
    │  llm_process ──────────────► finalize (首字响应点)       │
    │      │                                                  │
    │      └─(异步并行)─► rag_save + save_memory + live2d + tts│
    │                  (后处理不阻塞回复输出)                   │
    └──────────────────────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
     │  VTube       │   │  GPT-SoVITS  │   │  FastAPI     │
     │  Studio      │   │  / 火山引擎   │   │  WebUI      │
     │  WS :8001    │   │  HTTP API    │   │  :8081       │
     └──────────────┘   └──────────────┘   └──────────────┘
                                 │
                          ┌──────┴──────┐
                          ▼             ▼
                   ┌──────────┐  ┌──────────┐
                   │  Milvus  │  │   MinIO   │
                   │ :19530   │  │  :9000    │
                   └──────────┘  └──────────┘
```

---

## 🚀 快速开始

### 环境要求

| 组件 | 说明 |
|------|------|
| Python | 3.10+（推荐 3.12） |
| 操作系统 | Windows（Live2D / 视觉分析依赖 Win32 API） |
| VTube Studio | Live2D 功能必须，需启用 API 插件 |
| Milvus | RAG 检索，可 Docker 启动：`docker run -p 19530:19530 milvusdb/milvus` |
| Conda | 可选但推荐，方便依赖隔离 |

### 1. 克隆 & 安装

```bash
git clone https://github.com/your-username/LLM_vtuber.git
cd LLM_vtuber

# Conda 环境
conda create -n LLM python=3.12 -y
conda activate LLM

# 核心依赖
pip install langgraph==1.0.9 langchain-core==1.2.16
pip install openai==2.24.0 httpx websockets==16.0
pip install pymilvus==2.6.9 fastapi==0.141.1 uvicorn==0.52.3 sse-starlette==3.4.8
pip install pydantic==2.13.4 numpy==1.26.4
pip install playwright==1.62.0
playwright install chromium
pip install pyaudio==0.2.14 pydub==0.25.1 sounddevice==0.5.5
pip install pywin32==312 pillow python-dotenv==1.2.1

# 可选：从项目 requirements.txt 完整安装（注意路径依赖）
# pip install -r requirements.txt
```

### 2. 配置环境变量（必填项）

**所有密钥通过环境变量注入，config.ini 中对应字段留空。**

复制以下模板为 `.env` 文件（项目根目录）：

```bash
# ============================================================
# 🔑 必填：豆包大模型（ark.cn-beijing.volces.com）
# ============================================================
Doubao_API_KEY=your-doubao-api-key-here
Doubao_API_URL=https://ark.cn-beijing.volces.com/api/v3

# ============================================================
# 🎤 可选：火山引擎云端 TTS
# API Key 格式：从豆包语音控制台 → 应用管理 → Access Token 复制
# ============================================================
VOLCENGINE_API_KEY=
# 备选 key 名（自动查找优先级）：
# DOUBAO_API_KEY → VOLCENGINE_API_KEY → ARK_API_KEY → TTS_KEY

# ============================================================
# 📺 可选：B 站 SESSDATA（提高弹幕接收等级）
# 从浏览器 Cookie 复制 SESSDATA 字段
# ============================================================
BILIBILI_SESSDATA=

# ============================================================
# 📚 可选：Milvus + MinIO（RAG 知识库）
# ============================================================
MILVUS_TOKEN=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
KNOWLEDGE_BUCKET=knowledge-files
```

### 3. config.ini 关键项

config.ini 仅填写非敏感的运行时参数，所有密钥留空走环境变量：

```ini
[model]
model = doubao-seed-1-8-251228       # 主对话模型

[bilibili]
room_id = 30655190                    # 目标直播间号
sessdata =                            # 留空 → 用 $BILIBILI_SESSDATA

[live2d]
enabled = false                       # 启动时 --live2d 覆盖
host = localhost
port = 8001
sensitivity = 1.0
response_speed = 2.5                  # 动作过渡时长（秒）
smoothness = 0.8

[cloud_tts]
enabled = false
api_version = v3
resource_id = seed-tts-2.0            # seed-tts-2.0 或 seed-tts-1.0
voice_type = zh_female_vv_uranus_bigtts
format = pcm                          # pcm / wav；wav 需解析 RIFF 头

[milvus]
uri = http://localhost:19530
token =                               # 留空 → 用 $MILVUS_TOKEN
database = LLM_vtuber

[vision]
enabled = true
model = doubao-seed-1-6-vision-250815 # 视觉分析模型
```

### 4. 启动

```bash
# 最简（仅 AI 对话 + WebUI，不启动弹幕/Live2D）
python main.py

# 全功能
python main.py --all

# 按需组合
python main.py --live2d --cloud-tts --room-id 30655190
```

启动成功后控制台会打印：
```
[LangGraph] 初始化完成，WebUI 运行在 http://127.0.0.1:8081
```
浏览器打开该地址即可看到仪表盘。

### 命令行参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
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

---

## 📁 项目结构

```
LLM_vtuber/
├── main.py                    # 主入口：LangGraph 构建 + FastAPI + 消息队列 Worker
├── config.ini                 # 全局配置（非敏感运行时参数）
├── .env.example               # 环境变量模板（复制为 .env 后填写密钥）
├── requirements.txt           # Conda 完整导出（建议按上文核心依赖手动装）
│
├── LLM/                       # 🧠 LLM 对话核心
│   ├── LLM_node.py            # LangGraph 节点 + 意图分类器 + 工具轮次动态规划
│   ├── chat_model.py          # 豆包 API 调用 + function calling + reasoning_content 过滤
│   ├── live2d_models.py       # Live2DResponse Schema + TONE_TO_EXPRESSION 映射表
│   └── data_persistence.py    # 对话历史持久化
│
├── RAG/                       # 📚 检索增强生成
│   ├── RAG_node.py            # LangGraph RAG 节点
│   ├── Millvus_base.py        # Milvus 连接管理（单例 + 连接池）
│   ├── knowledge_base.py      # MinIO 文件 + Milvus 向量双存储
│   └── context_controller.py  # 上下文窗口管理 + 摘要器
│
├── live2d/                    # 🎭 Live2D 虚拟形象
│   ├── live2d_main.py         # 核心控制：idle_movement + update_non_moving_state + set_expression + dump_vts_current_params
│   ├── vtuber_studio_info.py  # VTube Studio WebSocket API 封装（auth + send_request）
│   └── live2d_controller_manager.py
│
├── audio/                     # 🔊 TTS 语音合成
│   ├── audio_main.py          # TTS LangGraph 节点（检查 enable_tts / enable_cloud_tts）
│   ├── cloud_tts.py           # 火山引擎 TTS V3 HTTP 单向流式 + JSON 流解析
│   ├── api.py                 # GPT-SoVITS HTTP API 客户端
│   └── tts_protocols.py       # TTS 协议抽象
│
├── broadcast/                 # 🎤 B 站弹幕监听
│   ├── bili_main.py           # 弹幕主进程（自动重载 blivedm）
│   ├── bilibili_state.py      # BilibiliCookie + 心跳管理（读取 $BILIBILI_SESSDATA）
│   ├── config.json            # {ROOM_IDS, output_port}
│   └── blivedm/               # 弹幕协议库（项目自带，无需额外 pip install）
│
├── tool/                      # 🛠️ 工具系统
│   ├── tool_node.py           # LangGraph 工具调度节点 + ToolRegistry
│   ├── browser_tool.py        # Playwright 浏览器 + SemanticSearchCache（BERT 相似度缓存）
│   └── vision_tool.py         # pygetwindow 截图 + 豆包视觉模型分析
│
├── webui/                     # 📊 WebUI 仪表盘
│   ├── app.py                 # FastAPI + WebSocket 后端（/ws/logs, /ws/status）
│   ├── api.py                 # REST API 路由（/api/send, /api/status, /api/logs, /api/control）
│   ├── state.py               # 运行时状态 + 日志缓冲区
│   └── index.html             # 前端单页应用（原生 JS + CSS Grid）
│
├── prompt/                    # 📝 角色配置
│   ├── character.json         # 角色人设 + 系统提示词模板
│   └── prompt_load.py         # 提示词加载器
│
├── danmu_tts/                 # 🔁 AI 读弹幕 TTS
├── vision_danmu/              # 👁️ 视觉弹幕（截图→视觉模型→AI 回复弹幕）
├── knowledge_base/             # 📖 知识库原始 .txt 数据（不入库）
├── docs/                      # 📄 详细技术文档
└── script/                    # 🧪 辅助脚本（创建数据库 / 导入数据）
```

---

## 🔧 核心机制

### LangGraph 状态流

```python
# main.py — LangGraphManager.build_graph()
graph = StateGraph(LLMState)
graph.add_node("init", ...)
graph.add_node("rag_retrieval", ...)
graph.add_node("llm_process", ...)      # 意图分类 + function calling
graph.add_node("rag_save", ...)          # 异步并行（不阻塞回复）
graph.add_node("live2d_action", ...)     # 表情注入 + 目光移动
graph.add_node("tool_dispatch", ...)     # 浏览器/视觉工具调度
graph.add_node("tts", ...)               # GPT-SoVITS / 火山引擎
graph.add_node("finalize", ...)
```

### Live2D 参数注入（核心原理）

VTube Studio 摄像头追踪会每秒更新一次参数。我们需要**更高频地注入**来覆盖它：

```python
# live2d_main.py — idle_movement 每 0.3s tick 一次
async def idle_movement(self):
    while self.running:
        async with self.operation_lock:
            params = self.core_params.copy()   # 基础值
            params += breath_wave(now)         # ±4.8° 呼吸 (0.15Hz / 4.2s 周期)
            params += drift_wave(now)          # ±2° 漂移抖动
            # 关键：face_found=True + mode="set" 覆盖追踪
            await self.inject_parameter_data(params, face_found=True, mode="set")
        await asyncio.sleep(_MOVE_STEP_SEC)    # ~0.3s
```

**重要**：注入参数名必须用 `InputParameterListRequest` 返回的人类可读格式（`FaceAngleX`, `MouthOpen`），而非 `Live2DParameterListRequest` 返回的 `Param158` 占位名。

### RAG 检索优化

| 优化 | 机制 | 效果 |
|------|------|------|
| **语义缓存** | Sentence-BERT 向量相似度 > 0.92 复用结果，TTL 30min | 减少 50% 重复搜索 |
| **检索短路** | 问候 / 短消息 / 意图 `skip` 跳过 Milvus | 减少无效 Milvus 调用 |
| **结果摘要** | 多搜索结果压缩为 {title, snippet, url} 文本 | 减少 LLM token |
| **意图预判** | 本地规则 + 关键词分类器（100% 准确率） | 动态减少工具轮次 |

### 云端 TTS（火山引擎）

```
POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
Headers: X-Api-Key: <volcengine-access-token>
Response: JSON 流（每个对象包含 code + data[base64]）
```

使用 `json.JSONDecoder.raw_decode` 逐流解析，不依赖换行符分割。

---

## 📊 WebUI API

### REST 端点

| 方法 | 路径 | 请求体 / 参数 | 说明 |
|------|------|------|------|
| POST | `/api/send` | `{"content": "你好"}` | 手动发送测试消息 |
| GET | `/api/status` | - | 完整运行状态 JSON（含 danmaku.enabled, live2d.online 等） |
| GET | `/api/logs` | `?limit=500&logger=Live2DMain` | 最近 N 条日志 |
| POST | `/api/control` | `{"action": "toggle_danmu"}` | 远程开关功能 |

### WebSocket

| 路径 | 推送内容 |
|------|------|
| `/ws/logs` | 实时日志行（含 logger / level / message / timestamp） |
| `/ws/status` | 状态变更事件 |

---

## 🐛 已知问题 & 故障排除

### Live2D 无反应 / 常态动作停止

| 症状 | 原因 | 解决方案 |
|------|------|------|
| VTS 报告 modelLoaded=false | VTS 模型加载有延迟，WebSocket 连接建立时模型还没就绪 | 启动后等待 3-5 秒再发弹幕测试 |
| 快照显示 VTS 参数名是 Param158 而非 FaceAngleX | 快照用错了 API（Live2DParameterListRequest vs InputParameterListRequest） | 代码已修复，确认日志里参数名正确 |
| 注入后 VTS 参数被追踪抢回 | 注入频率 < 1s 时会被摄像头追踪覆盖 | idle 循环必须 ≥ 每 0.5s 注入一次 |
| 表情推断失败（expression=None） | LLM 返回的语气词不在 TONE_TO_EXPRESSION 映射里 | 已加模糊匹配 fallback；可扩展映射表 |

### TTS / 音频

| 症状 | 原因 | 解决方案 |
|------|------|------|
| 云端 TTS 返回白噪音 | API 返回 MP3 但代码按 WAV 解析 RIFF 头 | config.ini 设 `format = wav` 或确保 JSON 流正确解析 |
| TTS 对 LLM 思考内容也生效 | `reasoning_content` 被当成回复 | 代码已在 chat_model.py 过滤 |
| 关闭 TTS 后仍请求 | `_trigger_parallel_post_process` 只检查模块可用性 | 代码已加 `self.enable_tts` / `self.enable_cloud_tts` 检查 |

### 弹幕

| 症状 | 原因 | 解决方案 |
|------|------|------|
| 停止监听后仍处理积压消息 | 停止时未清空队列 | 代码已加 queue.clear() |
| 仪表盘弹幕状态不同步 | 后端没返回 danmaku.enabled | 代码已补全 |

### 通用

| 症状 | 原因 | 解决方案 |
|------|------|------|
| `.gitignore` 意外排除核心文件 | 曾错误添加了 `tool/browser_tool.py` 和 `live2d/vtuber_studio_info.py` | 已从 .gitignore 移除（提交时会带出修复） |
| requirements.txt 无法在新环境安装 | 是 Conda 本地绝对路径导出 | 按上文「快速开始」核心依赖手动安装 |

---

## 📄 License

MIT License

---

## 🙏 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) — AI Agent 状态机框架
- [biliDm](https://github.com/czp3009/bilibili-api) — B 站弹幕协议
- [VTube Studio](https://github.com/DenchiSoft/VTubeStudio) — Live2D 表演软件 + API
- [Milvus](https://github.com/milvus-io/milvus) — 开源向量数据库
- [Playwright](https://github.com/microsoft/playwright) — 浏览器自动化
- [豆包 Doubao](https://www.volcengine.com/product/doubao) — 大模型 + TTS 服务

欢迎 Star 🌟 和 PR！
