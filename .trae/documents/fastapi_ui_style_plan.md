# FastAPI 风格前端界面改造计划

## 一、需求分析

### 1.1 核心目标
将当前 Web UI 控制台的界面风格从「暗紫色渐变科技风」改为「FastAPI 官方风格」——即 Swagger UI 所呈现的简洁、明亮、专业的浅色主题。

### 1.2 FastAPI 风格特征
- **配色**：白色背景 + 青蓝色主色（#1098ad）+ 浅灰辅助
- **布局**：简洁卡片式、清晰边框、适度阴影
- **字体**：系统无衬线字体，代码区域使用等宽字体
- **交互**：轻量过渡动画、焦点态有明显的蓝色光环
- **整体感觉**：专业、干净、文档工具风格

### 1.3 当前状态
- CSS 已在上一轮对话中改为浅色主题（基本完成）
- JavaScript 中仍有大量暗色主题的硬编码颜色值未更新
- `websocket.py` 是旧的 aiohttp 版本，已被 `app.py` 中的 FastAPI 实现替代

## 二、涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `webui/index.html` | 编辑 | 更新 JS 中硬编码颜色、内联样式、日志渲染颜色 |
| `webui/websocket.py` | 删除 | 旧 aiohttp 版，已被 app.py 中的 WebSocketManager 替代 |

## 三、详细步骤

### 步骤 1：完善 CSS 样式（index.html 中 `<style>` 块）
已在上轮完成。确认以下设计令牌（CSS Variables）正确：
- `--fastapi-primary: #1098ad`（FastAPI 标志性青蓝色）
- `--bg-body: #f5f6f8`（浅灰背景）
- `--bg-surface: #ffffff`（白色卡片）
- `--border: #e0e4ea`（柔和边框）
- `--text-primary: #2c3e50`（深色文字）

### 步骤 2：更新 JavaScript 中的硬编码颜色

需要修改的位置：

#### 2.1 空状态提示文字颜色
- 第 536 行：`color:#666` → `color:#95a5a6`（`--text-muted`）
- 第 564 行：`color:#666` → `color:#95a5a6`
- 第 847 行：`color:#666` → `color:#95a5a6`
- 第 962 行：`color:#666` → `color:#95a5a6`

#### 2.2 日志渲染颜色（renderLogs 函数）
```javascript
// 当前暗色主题颜色：
'INFO': '#4caf50', 'WARNING': '#ff9800', 'ERROR': '#f44336', ...

// 改为浅色主题颜色：
'INFO': '#28a745', 'WARNING': '#f0ad4e', 'ERROR': '#dc3545', ...
```
同时更新日志条目中的辅助文字颜色：
- `color:#666` → `color:#6c757d`
- `color:#ccc` → `color:#95a5a6`

#### 2.3 功能卡片内联样式（renderFeatureCards 函数）
移除按钮的 emoji 图标（▶️ ⏹️），改为更简洁的文字样式，符合 FastAPI 简洁风格。

#### 2.4 日志页面字体
- 第 641 行：`font-family: 'Consolas', 'Monaco', monospace` → 使用与 CSS 变量一致的等宽字体栈

### 步骤 3：清理废弃文件
删除 `webui/websocket.py`——该文件基于 aiohttp，已被 `app.py` 中的 `WebSocketManager` 类完全替代。

### 步骤 4：验证
- 启动项目，访问 `http://localhost:8081/`
- 检查所有页面（仪表盘、控制、消息、日志、配置、视觉）的显示效果
- 确认没有暗色主题残留
- 确认 WebSocket 实时推送正常
- 确认所有交互功能正常

## 四、风险与注意事项

1. **兼容性**：仅修改视觉样式，不改变任何 API 接口或数据结构，无兼容性风险
2. **内联样式**：所有需要修改的颜色值都在 JavaScript 中以字符串形式存在，需逐一排查
3. **websocket.py**：删除前确认没有其他文件引用它的导入
4. **验收标准**：页面呈现明亮简洁的 FastAPI/Swagger UI 风格，功能完全正常
