# LLM_vtuber 可视化控制面板实施计划

## 需求概述

为 LLM_vtuber 项目添加一个 Web 可视化界面，用于：
1. **控制配置**：在线修改 config.ini 参数（模型温度、功能开关等）
2. **查看消息**：实时查看弹幕消息和 AI 回复
3. **系统状态监控**：Live2D 连接状态、TTS 状态、队列长度等
4. **调试工具**：发送测试消息、触发视觉分析等

核心约束：**端口不能互相占用**。

---

## 一、端口分析与策略

### 1.1 当前端口占用情况

| 端口 | 用途 | 类型 |
|------|------|------|
| 8001 | VTube Studio WebSocket | Live2D 客户端→服务器 |
| 8081 | 主 HTTP 服务器 | 接收弹幕 POST |
| 8888 | Live2D HTTP 端口 | VTube Studio |
| 9880 | TTS 服务端口 | GPT-SoVITS |

### 1.2 端口复用策略

**方案：复用 8081 端口**，在现有 aiohttp 服务器上扩展路由。

理由：
- 8081 目前仅处理 `POST /`（弹幕转发），负载极低
- 同一 aiohttp app 可挂载路由和静态文件，无需额外端口
- 共享事件循环，可直接访问 LangGraphManager、消息队列等全局状态
- 避免新增端口带来的配置复杂度和防火墙问题

扩展后的 8081 路由：
```
POST /            → 弹幕转发（现有）
GET  /            → 可视化面板首页（新增）
GET  /api/status  → 系统状态 API（新增）
GET  /api/config  → 配置查询 API（新增）
POST /api/config  → 配置更新 API（新增）
GET  /api/messages → 消息历史 API（新增）
POST /api/send    → 发送测试消息（新增）
POST /api/vision  → 触发视觉分析（新增）
WS   /ws          → 实时推送 WebSocket（新增）
```

---

## 二、实现方案

### 2.1 新增文件

| 文件 | 描述 |
|------|------|
| `webui/` | Web UI 前端资源目录 |
| `webui/index.html` | 主页面（仪表盘+消息查看+配置+调试） |
| `webui/api.py` | API 路由处理函数集合 |
| `webui/websocket.py` | WebSocket 实时推送管理器 |

### 2.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `main.py` | 扩展 aiohttp 路由、添加 WebSocket、集成 API 处理 |
| `config.ini` | 添加 `[webui]` 配置段 |
| `tool/vision_tool.py` | 添加状态回调供 Web UI 查询 |

### 2.3 不修改的文件

- Live2D、TTS、RAG、browser_tool 等模块保持不变
- 仅通过 API 层暴露状态，不侵入核心业务逻辑

---

## 三、详细设计

### 3.1 前端页面 (webui/index.html)

单页面应用，采用纯原生 HTML/CSS/JS（无构建步骤），使用深色主题与 VTuber 风格匹配。

**页面布局**：

```
┌─────────────────────────────────────────────────────────┐
│  LLM_vtuber 控制台  │  状态: ● 运行中  │  队列: 2 │  🟢/🔴  │
├──────────┬──────────────────────────────────────────────┤
│          │                                              │
│ 📊 仪表盘 │  ┌──────────────────────────────────────┐   │
│ 📨 消息  │  │ 系统状态卡片                          │   │
│ ⚙️ 配置  │  │  Live2D: 已连接                       │   │
│ 👁️ 视觉  │  │  TTS: 就绪                           │   │
│          │  │  弹幕监听: 运行中                     │   │
│          │  │  API: 正常                           │   │
│          │  └──────────────────────────────────────┘   │
│          │                                              │
│          │  ┌──────────────────────────────────────┐   │
│          │  │ 实时消息流                           │   │
│          │  │  [弹幕] 用户名: 主播好厉害！          │   │
│          │  │  [AI] 【开心】谢谢夸奖~|up|open      │   │
│          │  │  [弹幕] 另一个用户: 晚上好！          │   │
│          │  │  [AI] 【普通】晚上好呀~|center|close │   │
│          │  └──────────────────────────────────────┘   │
│          │                                              │
│          │  ┌──────────────────────────────────────┐   │
│          │  │ 快速操作                             │   │
│          │  │  [发送测试消息] [截图] [视觉分析]     │   │
│          │  └──────────────────────────────────────┘   │
│          │                                              │
├──────────┴──────────────────────────────────────────────┤
│  底部状态栏: 端口 8081 | 运行时间: 2h 35m | v1.0      │
└─────────────────────────────────────────────────────────┘
```

**核心交互**：
- Sidebar 切换面板
- 实时消息流自动滚动（通过 WebSocket 推送）
- 配置面板可在线编辑并保存
- 视觉分析面板：列出窗口 → 选择 → 截图 → 分析

### 3.2 API 设计 (webui/api.py)

```python
# GET /api/status
async def get_status(request):
    """返回系统状态"""
    return {
        "live2d": {"enabled": True, "connected": True, "host": "localhost", "port": 8001},
        "tts": {"enabled": True, "status": "ready", "port": 9880},
        "danmaku": {"enabled": True, "room_id": "904823", "status": "listening"},
        "model": {"name": "doubao-seed-1-8-251228", "temperature": 0.7},
        "queue": {"size": 2, "max_size": 10},
        "uptime": 9300,  # 秒
        "messages_processed": 156
    }

# GET /api/config
async def get_config(request):
    """返回当前配置（可编辑部分）"""
    
# POST /api/config
async def update_config(request):
    """更新配置（部分更新，如 {temperature: 0.8}）"""
    
# GET /api/messages?limit=50
async def get_messages(request):
    """返回最近 N 条消息历史"""
    
# POST /api/send
async def send_test_message(request):
    """发送测试消息到队列"""
    # body: {"content": "你好", "name": "测试用户"}
    
# POST /api/vision
async def trigger_vision(request):
    """触发视觉分析"""
    # body: {"target": "desktop", "prompt": ""}
    
# GET /api/windows
async def list_windows(request):
    """列出系统可见窗口"""
```

### 3.3 WebSocket 实时推送 (webui/websocket.py)

```python
class WebSocketManager:
    """管理 WebSocket 连接，广播实时消息"""
    
    async def broadcast(self, message: dict):
        """向所有连接的客户端广播消息"""
        
    async def on_new_message(self, msg_type, content, ...):
        """新消息事件：弹幕、AI 回复、系统状态变更等"""
```

**推送消息格式**：
```json
{
    "type": "danmaku|ai_response|system|vision",
    "data": { ... },
    "timestamp": 1720000000
}
```

### 3.4 历史消息存储

新增内存环形缓冲区（不依赖数据库）：
```python
# 在 main.py 中
_message_history = deque(maxlen=500)  # 保存最近 500 条消息
```

消息类型：
- `danmaku`: 用户弹幕
- `ai_response`: AI 回复（含解析后的语气/动作）
- `system`: 系统日志（Live2D 连接、TTS 状态变化等）
- `vision`: 视觉分析结果

### 3.5 main.py 集成

在现有 `start_http_server` 函数中扩展路由：

```python
async def start_http_server(port=8081):
    app = web.Application()
    
    # 现有路由
    app.add_routes([
        web.post('/', handle_post_request),
    ])
    
    # 新增：API 路由
    from webui.api import setup_api_routes
    setup_api_routes(app)
    
    # 新增：静态文件
    app.router.add_static('/webui', path='webui', name='webui')
    
    # 新增：WebSocket
    from webui.websocket import ws_manager
    app.add_routes([web.get('/ws', ws_manager.websocket_handler)])
    
    # ... 启动服务器
```

### 3.6 config.ini 扩展

```ini
[webui]
# 是否启用 Web 控制台
enabled = true
# 控制台路径前缀
path = /webui
# 历史消息保留条数
history_size = 500
# 会话超时（秒）
session_timeout = 3600
```

---

## 四、执行步骤

### 步骤 1：创建 webui 模块
1. 创建 `webui/` 目录
2. 创建 `webui/websocket.py` - WebSocket 管理器
3. 创建 `webui/api.py` - API 路由处理

### 步骤 2：创建前端页面
1. 创建 `webui/index.html` - 单页面应用（仪表盘+消息+配置+视觉）

### 步骤 3：集成到 main.py
1. 扩展 `start_http_server` 函数
2. 添加消息历史记录逻辑
3. 在关键节点触发 WebSocket 推送

### 步骤 4：添加配置
1. 在 `config.ini` 中添加 `[webui]` 段

### 步骤 5：测试验证
1. 启动项目，访问 `http://localhost:8081/` 
2. 测试各面板功能
3. 验证端口无冲突

---

## 五、风险与注意事项

1. **端口冲突**：复用 8081 端口，无需新增端口。但需确保 `/` 路径不与现有 POST 冲突
   - 现有：`POST /` → 弹幕转发
   - 新增：`GET /` → 返回 index.html
   - HTTP 方法不同，无冲突

2. **线程安全**：WebSocket 推送和消息记录需使用 asyncio 锁保护

3. **内存占用**：历史消息环形缓冲区 maxlen=500，内存占用可控

4. **安全**：当前为本地开发工具，无认证。如需公网部署需添加认证层

5. **浏览器兼容**：使用 ES6+ 语法，需现代浏览器（Chrome/Edge/Firefox 最新版）

6. **前端技术选型**：纯原生 HTML/CSS/JS，零依赖零构建，降低部署复杂度