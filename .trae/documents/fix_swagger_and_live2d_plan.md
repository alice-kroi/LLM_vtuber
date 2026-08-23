# Swagger UI CDN 错误 & Live2D 状态不同步修复计划

## 一、问题分析

### 问题 1：Swagger UI CDN 资源加载失败
- **现象**：访问 `http://localhost:8081/docs` 时，浏览器控制台报错 `net::ERR_NAME_NOT_RESOLVED`，无法从 `cdn.jsdelivr.net` 加载 Swagger UI 的 CSS/JS 文件
- **根因**：FastAPI 的 `/docs` 和 `/redoc` 端点默认从 CDN 加载静态资源，用户网络环境无法访问该 CDN
- **影响**：Swagger UI 和 ReDoc 页面完全不可用，且产生错误日志
- **解决方案**：禁用 FastAPI 的 `docs_url` 和 `redoc_url`（设置为 `None`），因为项目已有自定义 Web UI 控制台

### 问题 2：Live2D 连接后状态不更新
- **现象**：Live2D 已成功连接（VTube Studio 中可见），但 Web UI 上仍显示异常/未启用
- **根因分析**：
  1. **状态判断逻辑有缺陷**（`app.py` 第 240 行）：`if app_main.args and app_main.args.live2d` 这个前置条件过严——如果 `args.live2d` 为 `True` 但 `live2d_manager` 尚未初始化完成，就会直接跳到 `connection_failed` 分支
  2. **缺少 WebSocket 通知**：`start_feature`/`stop_feature` 端点通过 `asyncio.create_task()` 异步执行，成功后没有广播 `status_change` 事件，前端不知道状态已变更
  3. **状态轮询间隔太长**：前端每 5 秒轮询一次 `/api/status`，如果在轮询间隔内连接成功，状态可能不会及时更新

## 二、涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `webui/app.py` | 编辑 | 1) 禁用 docs/redoc CDN 页面；2) 修复 get_status 逻辑；3) 添加状态变更广播 |

## 三、详细步骤

### 步骤 1：禁用 Swagger UI CDN 页面
在 `app.py` 第 194-200 行，将：
```python
app = FastAPI(
    ...
    docs_url="/docs",
    redoc_url="/redoc",
)
```
改为：
```python
app = FastAPI(
    ...
    docs_url=None,
    redoc_url=None,
)
```

### 步骤 2：修复 get_status 中的 Live2D 状态判断逻辑
将状态判断逻辑改为**以 `live2d_manager.is_connected` 为主要依据**，而非 `args.live2d` 标志：

```python
# 当前逻辑（有缺陷）：
if app_main.args and app_main.args.live2d:
    if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
        live2d_connected = True
        live2d_status = "connected"
    else:
        live2d_status = "connection_failed"

# 修复后：直接以 manager 状态为准
if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
    live2d_connected = True
    live2d_status = "connected"
elif app_main.args and app_main.args.live2d:
    live2d_status = "connection_failed"
else:
    live2d_status = "not_enabled"
```

同步修复 `get_features` 端点（第 420-467 行）中的相同问题。

### 步骤 3：添加 WebSocket 状态变更广播
在 `start_feature` 和 `stop_feature` 的异步任务完成后，添加状态变更广播：

```python
async def start():
    result = False
    if name == "live2d" and not (...):
        result = await app_main.enable_live2d()
    elif name == "tts" and not (...):
        result = await app_main.enable_tts()
    elif name == "danmaku_listener" and not (...):
        result = await app_main.start_danmaku_listener()
    # 广播状态变更
    await _ws_manager.broadcast_status_change(name, "started" if result else "failed")
```

### 步骤 4：验证
- 启动项目，确认无 CDN 相关错误
- 启动 Live2D，验证 UI 上状态实时更新为「已连接」
- 停止 Live2D，验证状态更新为「已停止」

## 四、风险与注意事项
1. **禁用 Swagger UI**：用户无法通过 `/docs` 访问 API 文档，但项目已有完整的自定义控制台
2. **状态逻辑变更**：以 `is_connected` 为准更可靠，但需确保不会误报
3. **WebSocket 广播**：添加广播不会影响现有功能，仅增强状态同步
