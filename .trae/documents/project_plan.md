# LLM_vtuber 项目实施计划

## 需求概述

1. **重新整理项目说明书文档**：将现有的零散文档整合成一份结构化的完整说明书
2. **添加视觉能力**：让项目能够识别后台指定窗口画面，回答"我的桌面有什么"

---

## 一、说明书文档整理

### 1.1 当前状态

- `docs/main.md`：仅为简短的需求草稿，非正式文档
- `docs/vtuberapi.md`：VTube Studio API 参考文档（第三方）
- `docs/live2d_design.md`：Live2D 设计需求草稿
- `docs/live2d_algorithm.md`：Live2D 算法说明
- `docs/graph_state_structure.md`：图状态结构说明
- `docs/RAG_node_documentation.md`：RAG 节点文档
- `docs/milvus_chat_data_design.md`：Milvus 聊天数据设计
- `docs/edge_browser_tool_design.md`：浏览器工具设计

### 1.2 执行方案

创建一份完整的 `docs/PROJECT_README.md`，包含以下章节：

1. **项目概述**：项目定位、核心功能、技术栈
2. **架构设计**：LangGraph 流程图、模块关系图
3. **快速开始**：环境配置、启动命令、命令行参数说明
4. **模块说明**：
   - 主程序（main.py）
   - LLM 节点（LLM_node.py / chat_model.py）
   - RAG 检索（RAG_node.py / Millvus_base.py）
   - Live2D 控制（live2d/）
   - TTS 语音合成（audio/）
   - 哔哩哔哩监听（broadcast/）
   - 工具系统（tool/）
5. **配置说明**：config.ini 各段详细说明
6. **API 接口**：HTTP 服务器接口、消息格式规范
7. **扩展指南**：如何添加新工具、新节点

### 1.3 涉及文件

- **创建**：`docs/PROJECT_README.md`

---

## 二、添加视觉能力（窗口识别）

### 2.1 技术方案

#### 核心流程

```
用户问"我的桌面有什么" 
    → LLM 识别意图，调用 vision_analyze 工具
    → 工具截取指定窗口画面
    → 调用豆包多模态模型（doubao-vl）分析图片
    → 返回分析结果
    → LLM 整合结果生成自然语言回复
```

#### 依赖库

```python
# 窗口截图（Windows 环境）
pip install pygetwindow      # 获取窗口列表、指定窗口截图
pip install pyrect            # 窗口坐标处理
# 或者使用更底层的 win32 API
pip install pywin32           # Windows API 访问
pip install Pillow            # 图像处理（已安装）

# 多模态 LLM
# 使用豆包 doubao-vision-pro 模型
```

#### 工具设计

**新文件**：`tool/vision_tool.py`

```python
# 核心函数
async def capture_window_screenshot(window_title: str = None) -> Dict:
    """截取指定窗口或全屏截图"""
    
async def vision_analyze(target: str = "desktop") -> Dict:
    """
    视觉分析工具
    - target: 分析目标 (desktop, window:窗口标题)
    - 返回: 分析结果描述
    """

# 工具 Schema（供 LLM function calling）
VISION_TOOLS_SCHEMA = [...]
```

#### 配置扩展

在 `config.ini` 中新增 `[vision]` 段：
```ini
[vision]
# 是否启用视觉能力
enabled = true
# 默认截图目标窗口标题（留空为全屏）
default_window = 
# 截图保存路径
screenshot_dir = ./screenshots
# 多模态模型名称
model = doubao-vision-pro-32k
# 模型 API 地址
api_url = 
```

#### LLM 集成

在 `chat_model.py` 中注册视觉工具到 function calling 列表，让 LLM 在回答"桌面有什么"等问题时自动调用视觉分析工具。

### 2.2 涉及文件

- **创建**：`tool/vision_tool.py` - 视觉分析工具模块
- **修改**：`tool/tool_node.py` - 注册新的视觉工具
- **修改**：`LLM/chat_model.py` - 集成视觉工具到 LLM function calling
- **修改**：`config.ini` - 添加 `[vision]` 配置段
- **修改**：`main.py` - 在初始化中加载视觉工具配置

### 2.3 关键实现细节

#### 窗口截图实现

```python
import pygetwindow as gw
from PIL import Image
import io

async def capture_window(window_title=None):
    """
    截取指定窗口或全屏
    - window_title: 窗口标题关键词，None 则全屏
    """
    if window_title:
        windows = gw.getWindowsWithTitle(window_title)
        if not windows:
            return {"success": False, "error": f"未找到窗口: {window_title}"}
        window = windows[0]
        # 使用 window.left, window.top, window.width, window.height
        screenshot = window.screenshot()
    else:
        # 全屏截图
        import pyautogui
        screenshot = pyautogui.screenshot()
    
    # 转换为 base64 供 API 调用
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    return buffer.getvalue()
```

#### 多模态调用

```python
def analyze_image_base64(image_b64, prompt="描述这张图片"):
    """调用豆包视觉模型分析图片"""
    client = OpenAI(api_key=...)
    response = client.chat.completions.create(
        model="doubao-vision-pro-32k",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
            ]
        }]
    )
    return response.choices[0].message.content
```

---

## 三、执行步骤

### 步骤 1：整理文档
1. 创建 `docs/PROJECT_README.md`
2. 整合现有文档内容，形成完整说明书

### 步骤 2：实现视觉工具
1. 创建 `tool/vision_tool.py` 实现窗口截图 + 视觉分析
2. 在 `tool/tool_node.py` 中注册视觉工具
3. 在 `LLM/chat_model.py` 中集成视觉工具到 function calling
4. 在 `config.ini` 中添加 `[vision]` 配置段
5. 在 `main.py` 中加载视觉配置

### 步骤 3：测试验证
1. 测试窗口截图功能
2. 测试视觉分析功能
3. 测试完整流程（用户提问 → LLM 调用工具 → 返回结果）

---

## 四、风险与注意事项

1. **Windows 窗口兼容性**：`pygetwindow` 依赖 Windows API，需在 Windows 环境测试
2. **模型选择**：需要用户确认可用的豆包多模态模型名称
3. **性能**：截图 + 视觉分析耗时可能较长（3-10秒），需要合理的超时设置
4. **隐私**：全屏截图可能包含敏感信息，需在文档中提醒用户
5. **依赖安装**：需要安装 `pygetwindow`、`pyrect`、`Pillow`（若未安装）