# LLM_vtuber 功能扩展实施计划

## 需求概述

用户需要两项功能改进：
1. **日志消息界面**：添加新页面展示所有日志消息（不仅是弹幕/AI回复），支持按类型筛选
2. **按需启动模式**：先启动 Web 控制台，通过按钮动态启停功能（Live2D、TTS、弹幕监听等），而非启动时一次性全加载

## 研究结论

### 当前架构
- **前端** ([index.html](file:///e:/GitHub/LLM_vtuber/webui/index.html))：4个页面（仪表盘/消息/配置/视觉），通过导航切换
- **API层** ([api.py](file:///e:/GitHub/LLM_vtuber/webui/api.py))：提供 status/config/messages/send/vision 等端点
- **后端** ([main.py](file:///e:/GitHub/LLM_vtuber/main.py))：启动时一次性初始化所有功能，LangGraph 图结构在启动时固定

### 关键技术点
- **日志收集**：当前 Python logging 输出到控制台，需要添加内存收集器供 Web 查询
- **动态功能**：LangGraph 图结构在 `build_graph()` 时确定，动态启停需要支持重新编译图

---

## 修改计划

### 任务 1：添加日志收集器

**目标**：在内存中收集日志消息，供 Web UI 查询

**修改文件**：
1. `webui/api.py` — 添加内存日志收集器
2. `webui/index.html` — 添加日志页面 UI

**步骤**：
1. 在 `api.py` 中添加 `_log_buffer`（deque，最大 2000 条）和自定义 `LogHandler`，将日志同时输出到内存
2. 添加 `get_logs()` API 端点（`GET /api/logs`），支持按 level/type 筛选
3. 在 `index.html` 侧边栏添加"日志"导航项
4. 添加日志页面 UI：表格形式展示日志，支持按级别（INFO/WARNING/ERROR）和类型筛选
5. 支持 WebSocket 实时推送新日志

### 任务 2：添加功能控制面板

**目标**：支持通过 Web UI 动态启停项目功能

**修改文件**：
1. `main.py` — 分离启动逻辑，支持动态启停
2. `webui/api.py` — 添加功能控制 API
3. `webui/index.html` — 添加控制面板 UI

**步骤**：
1. **重构 main.py**：
   - 将初始化逻辑拆分为独立函数：`init_live2d()`、`init_tts()`、`start_danmaku_listener()` 等
   - LangGraphManager 添加 `enable_feature()` / `disable_feature()` 方法，支持重新编译图
   - 默认启动时只加载 Web 控制台，不自动初始化功能

2. **添加 API 端点**（`api.py`）：
   - `GET /api/features` — 查询各功能状态
   - `POST /api/features/{name}/start` — 启动指定功能
   - `POST /api/features/{name}/stop` — 停止指定功能
   - 支持的功能：live2d、tts、danmaku_listener

3. **添加控制面板 UI**（`index.html`）：
   - 侧边栏添加"控制"导航项
   - 控制面板显示各功能卡片（状态、配置、启停按钮）
   - 实时状态更新

### 任务 3：修改启动流程

**修改文件**：`main.py`

**步骤**：
1. 修改 `main()` 函数：
   - 默认模式（无参数）：只启动 Web 控制台，等待用户通过 UI 启停功能
   - 兼容模式（`--all` 参数）：启动所有功能（向后兼容）
   - 单独启动：`--live2d`、`--tts` 等参数直接启动指定功能

2. 启动顺序：`_cleanup_port` → 初始化 Web 控制台 → 等待用户操作

---

## 风险评估

| 风险点 | 影响 | 缓解措施 |
|--------|------|----------|
| LangGraph 动态重新编译 | 中等 | 功能切换时短暂阻塞，记录当前消息队列状态并等待处理完成 |
| 日志内存占用 | 低 | 设置 deque 上限 2000 条，自动淘汰旧日志 |
| 弹幕监听器进程管理 | 低 | 已有 `_bili_process` 引用，需添加启停 API |
| WebSocket 并发日志推送 | 低 | 复用现有 `ws_manager`，添加日志广播方法 |

## 依赖关系

- 任务 1 和任务 2 相互独立，可并行实施
- 任务 3 依赖任务 2 的 API 和 UI 完成

## 实施顺序

1. 任务 1：日志收集器 + 日志页面（独立）
2. 任务 2：功能控制面板（独立）
3. 任务 3：修改启动流程（依赖任务 2）
