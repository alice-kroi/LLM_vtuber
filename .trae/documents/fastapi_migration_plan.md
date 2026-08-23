# FastAPI 迁移实施计划

## 需求概述

将项目的 Web UI 控制台从 **aiohttp** 迁移到 **FastAPI**，使用 uvicorn 作为 ASGI 服务器。项目中已有 FastAPI 使用先例（`audio/api.py`），具备技术基础。

## 研究结论

### 当前架构
- **Web 框架**: aiohttp（`webui/api.py` 使用 `aiohttp.web` 编写路由）
- **启动方式**: `main.py` 中通过 `web.AppRunner` + `web.TCPSite` 启动
- **WebSocket**: `webui/websocket.py` 使用 `aiohttp.WebSocketResponse` 实现
- **前端**: 单文件 `webui/index.html`，原生 JS，通过 fetch/WebSocket 与后端通信
- **已有 FastAPI**: `audio/api.py` 和 `audio/api_v2.py` 使用 FastAPI + uvicorn

### 目标架构
- **Web 框架**: FastAPI + uvicorn
- **API 文档**: 自动生成 Swagger UI（`/docs`）和 ReDoc（`/redoc`）
- **WebSocket**: FastAPI 原生 WebSocket 支持
- **前端**: 保持 `index.html` 不变，仅需调整少量 API 调用方式

### API 端点映射

| aiohttp (当前) | FastAPI (目标) | 功能 |
|---|---|---|
| `GET /api/status` | `GET /api/status` | 系统状态 |
| `GET /api/config` | `GET /api/config` | 获取配置 |
| `POST /api/config` | `POST /api/config` | 更新配置 |
| `GET /api/messages` | `GET /api/messages` | 消息历史 |
| `POST /api/send` | `POST /api/send` | 发送消息 |
| `POST /api/vision` | `POST /api/vision` | 视觉分析 |
| `GET /api/windows` | `GET /api/windows` | 列出窗口 |
| `GET /api/logs` | `GET /api/logs` | 系统日志 |
| `GET /api/features` | `GET /api/features` | 功能状态 |
| `POST /api/features/{name}/start` | `POST /api/features/{name}/start` | 启动功能 |
| `POST /api/features/{name}/stop` | `POST /api/features/{name}/stop` | 停止功能 |
| `GET /ws` | `WebSocket /ws` | WebSocket |
| `GET /` | `GET /` | 前端页面 |

---

## 修改计划

### 任务 1：创建 FastAPI 应用核心文件

**新建文件**: `webui/app.py`

**步骤**：
1. 创建 FastAPI 应用实例
2. 实现所有 REST API 路由（从 `api.py` 迁移）
3. 实现 WebSocket 端点（从 `websocket.py` 迁移）
4. 实现静态文件服务（`index.html`）
5. 添加 CORS 中间件（如果需要）
6. 添加启动/关闭事件钩子

**关键设计**：
- FastAPI 路由函数使用 `async def`，参数通过 `FastAPI` 的依赖注入系统获取
- 保持与现有前端完全兼容的 API 格式（JSON 结构不变）
- 使用 Pydantic 模型定义请求/响应体（可选，提升类型安全）

### 任务 2：重构 WebSocket 管理器

**修改文件**: `webui/websocket.py`

**步骤**：
1. 将 `WebSocketManager` 改为 FastAPI 兼容的实现
2. 使用 `fastapi.WebSocket` 替代 `aiohttp.WebSocketResponse`
3. 保持所有 `broadcast_*` 方法签名不变

### 任务 3：迁移 API 路由

**修改文件**: `webui/api.py` → 重构为 FastAPI 路由

**步骤**：
1. 将每个 handler 从 aiohttp 签名改为 FastAPI 签名
2. `web.Request` → `fastapi.Request` 或直接参数注入
3. `web.json_response()` → 使用 `return dict()` 或 `JSONResponse`
4. `web.Response` → `Response` 或直接返回字节/字符串

### 任务 4：修改 main.py 启动流程

**修改文件**: `main.py`

**步骤**：
1. 移除 aiohttp 相关导入和启动逻辑
2. 改为通过 uvicorn 启动 FastAPI 应用
3. 保持异步启动/停止能力（uvicorn 可通过 `uvicorn.Server` 实现）

### 任务 5：前端兼容性调整

**修改文件**: `webui/index.html`（可能少量调整）

**步骤**：
1. 检查 API 调用方式是否兼容
2. 检查 WebSocket 连接路径是否兼容
3. 确保错误处理正确

---

## 技术要点

### FastAPI vs aiohttp 差异

| 方面 | aiohttp | FastAPI |
|---|---|---|
| 路由注册 | `app.add_routes([web.get('/path', handler)])` | `@app.get('/path')` 装饰器 |
| 请求参数 | `request.query.get('key')` | `key: str = Query(...)` |
| 请求体 | `await request.json()` | `body: Model` 自动解析 |
| 响应 | `web.json_response(data)` | `return data` 自动序列化 |
| WebSocket | `web.WebSocketResponse` | `WebSocket` 类依赖 |
| 启动 | `AppRunner + TCPSite` | `uvicorn.run(app)` |
| ASGI 兼容 | 否 | 是（可运行在任何 ASGI 服务器） |

### 依赖项
- `fastapi`（已安装，audio/api.py 已使用）
- `uvicorn`（已安装，audio/api.py 已使用）

### 风险处理
| 风险 | 影响 | 缓解措施 |
|---|---|---|
| WebSocket 行为差异 | 中等 | 保留完整的 broadcast 方法接口，内部实现适配 |
| API 响应格式变化 | 低 | 严格保持现有 JSON 结构 |
| 启动流程复杂性 | 低 | 使用 `uvicorn.Server` 类实现异步启停 |
| 静态文件服务 | 低 | 使用 FastAPI 的 `StaticFiles` |

## 实施顺序

1. 创建 `webui/app.py`（FastAPI 应用核心）
2. 重构 `webui/websocket.py`（WebSocket 管理）
3. 修改 `main.py`（启动流程）
4. 前端兼容性调整（如需要）
5. 测试验证
