# Edge 浏览器信息获取工具 — 实现方案

## 1. 功能目标

让 LLM 能够自主调用 Edge 浏览器获取实时网页信息（搜索、抓取页面内容），用于回答需要联网数据的问题（如时事新闻、天气、股票、技术文档等）。

## 2. 现有架构分析

### 当前 LangGraph 图流程

```
START → init → rag_retrieval → llm_process → rag_save → [live2d → tts →] finalize
```

### 关键现状

| 组件 | 现状 | 需要改动 |
|------|------|----------|
| [chat_model.py](file:///e:/GitHub/LLM_vtuber/LLM/chat_model.py) | `doubao_chat_node` 用 OpenAI SDK 直接调用，**未绑定 tools** | 需增加 `tools=` 参数和 tool_call 解析 |
| [tool_node.py](file:///e:/GitHub/LLM_vtuber/tool/tool_node.py) | 有 `ToolRegistry` + `tool_dispatch_node`，已注册 4 个示例工具，**但未接入 LangGraph 图** | 需注册浏览器工具并接入图 |
| [LLM_node.py](file:///e:/GitHub/LLM_vtuber/LLM/LLM_node.py) | `_run_doubao_chat` 不处理 tool_call 循环 | 需增加 tool_call 循环逻辑 |
| [main.py](file:///e:/GitHub/LLM_vtuber/main.py) `build_graph` | 图中无 tool_dispatch 节点 | 需添加节点和条件边 |

## 3. 技术选型

### 浏览器自动化方案对比

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **Playwright (async)** | 原生 async 与项目契合、API 现代化、支持 Edge/Chromium、自动管理浏览器 | 需额外安装 `playwright` 包 + 浏览器二进制 | **推荐** |
| Selenium + EdgeDriver | 生态成熟 | 同步 API 阻塞事件循环、需手动管理 Driver 版本 | 不推荐 |
| pyppeteer | 纯 Python | 维护不活跃、文档少 | 不推荐 |

### 选定方案：Playwright async + Chromium (Edge 内核)

- Playwright 的 `chromium` channel 可直接启动 Edge 浏览器
- async API 与项目现有 `asyncio` 事件循环无缝集成
- 支持 headless 模式，不影响服务器运行

### 依赖

```bash
pip install playwright
playwright install chromium   # 安装浏览器二进制（约 150MB）
```

## 4. 模块设计

### 4.1 新增文件

```
tool/
├── tool_node.py          # 已有，修改
├── browser_tool.py       # 新增：浏览器工具实现
```

### 4.2 `browser_tool.py` 模块结构

```python
"""Edge 浏览器信息获取工具"""
import asyncio
import logging
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    浏览器连接管理器（单例，复用浏览器实例）

    - 应用启动时创建一次 Browser 实例
    - 每次工具调用创建独立 BrowserContext（隔离 Cookie/缓存）
    - 调用结束后关闭 Context，保留 Browser
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def start(self, headless: bool = True):
        """启动浏览器（应用初始化时调用一次）"""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                channel="msedge",      # 使用系统安装的 Edge
                args=["--disable-gpu", "--no-sandbox"]
            )
            logger.info("Edge 浏览器已启动 (headless=%s)", headless)

    async def stop(self):
        """关闭浏览器（应用退出时调用）"""
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Edge 浏览器已关闭")

    async def new_context(self) -> BrowserContext:
        """创建新的浏览器上下文（每次工具调用独立隔离）"""
        if not self._browser:
            await self.start()
        return await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            timeout=30000          # 页面操作超时 30s
        )


# 全局单例
browser_manager = BrowserManager()
```

### 4.3 工具函数定义

提供两个核心工具，覆盖大部分信息获取场景：

#### 工具 1：`web_search` — 网页搜索

```python
async def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    使用 Edge 浏览器进行网页搜索，返回搜索结果列表。

    Args:
        query: 搜索关键词
        max_results: 最大返回结果数（默认 5）

    Returns:
        {
            "success": True,
            "results": [
                {"title": "标题", "url": "链接", "snippet": "摘要"},
                ...
            ]
        }
    """
```

**实现逻辑**：
1. 创建 BrowserContext → 打开新页面
2. 导航到 Bing 搜索（`https://www.bing.com/search?q={query}`）
3. 等待搜索结果加载，解析 `#b_results .b_algo` 元素
4. 提取标题、链接、摘要文本
5. 关闭 Context，返回结构化结果

#### 工具 2：`fetch_webpage` — 抓取网页内容

```python
async def fetch_webpage(url: str, extract_mode: str = "text", max_length: int = 5000) -> Dict[str, Any]:
    """
    抓取指定 URL 的网页内容。

    Args:
        url: 目标网页 URL
        extract_mode: 提取模式 — "text"(纯文本) | "article"(正文主体) | "html"(原始HTML)
        max_length: 返回内容最大字符数（默认 5000，避免超长内容消耗 token）

    Returns:
        {
            "success": True,
            "title": "页面标题",
            "content": "页面内容文本（截断到 max_length）",
            "url": "最终URL（可能经过重定向）"
        }
    """
```

**实现逻辑**：
1. 创建 BrowserContext → 打开新页面
2. 导航到 URL，等待 `networkidle`
3. 根据 `extract_mode`：
   - `text`：提取 `body.innerText`
   - `article`：优先提取 `<article>` 或 `<main>` 标签内容，回退到 `body`
   - `html`：提取 `body.innerHTML`
4. 截断到 `max_length`，返回标题 + 内容
5. 关闭 Context

### 4.4 工具 Schema 定义（供 LLM 调用）

使用 OpenAI function calling 格式：

```python
BROWSER_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "使用搜索引擎搜索网页信息，返回标题、链接和摘要。适用于需要查询最新信息、新闻、天气等场景。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "最大返回结果数", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "抓取指定URL的网页正文内容。适用于需要获取具体网页详细信息的场景。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页URL"},
                    "extract_mode": {"type": "string", "enum": ["text", "article", "html"], "default": "text"},
                    "max_length": {"type": "integer", "description": "返回内容最大字符数", "default": 5000}
                },
                "required": ["url"]
            }
        }
    }
]
```

## 5. 集成方案

### 5.1 LangGraph 图改造

在 `llm_process` 节点后增加条件边，检测 LLM 响应中是否包含 `tool_calls`：

```
START → init → rag_retrieval → llm_process → [条件判断]
                                            ├─ 有 tool_calls → tool_dispatch → llm_process (二次调用)
                                            └─ 无 tool_calls → rag_save → [live2d → tts →] finalize
```

### 5.2 `chat_model.py` 改动

在 `_call_chat_api_with_retry` 中增加 `tools` 参数支持：

```python
def _call_chat_api_with_retry(state: ChatState, api_key: str, base_url: str,
                               tools: list = None) -> dict:
    # ...
    kwargs = {
        "model": state["model"],
        "messages": openai_messages,
        "temperature": state["temperature"],
        "max_tokens": state["max_tokens"],
        "stream": False
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp_obj = client.chat.completions.create(**kwargs)

    # 解析 tool_calls
    msg = resp_obj.choices[0].message
    tool_calls = msg.tool_calls if hasattr(msg, "tool_calls") else None
    # ...
    return {"response": ai_resp, "tokens_used": tokens, "tool_calls": tool_calls}
```

### 5.3 `LLM_node.py` 改动

`_run_doubao_chat` 增加 tool_call 循环：

```python
def _run_doubao_chat(state: LLMState, *, with_context: bool) -> dict:
    # ... 构建 system_prompt ...

    # 第一轮调用（可能返回 tool_calls）
    result = doubao_chat_node(chat_state, tools=BROWSER_TOOLS_SCHEMA)

    # 如果 LLM 返回了 tool_calls，执行工具后再次调用 LLM
    tool_calls = result.get("tool_calls")
    if tool_calls:
        # 1. 将 assistant 的 tool_call 消息加入历史
        # 2. 调用 tool_dispatch_node 执行工具
        # 3. 将工具结果作为 tool 角色消息加入历史
        # 4. 再次调用 LLM，让模型基于工具结果生成最终回复
        tool_results = tool_registry.execute_tool_calls(tool_calls)
        # 将 tool_results 拼入 messages
        chat_state["messages"] = chat_state["messages"] + [
            {"role": "assistant", "tool_calls": tool_calls},
            *[{"role": "tool", "content": r["result"]} for r in tool_results]
        ]
        result = doubao_chat_node(chat_state, tools=None)  # 第二轮不再传 tools

    return {"response": result.get("response"), ...}
```

### 5.4 `tool_node.py` 改动

注册浏览器工具（需适配 async）：

```python
# 在文件末尾注册
from tool.browser_tool import web_search, fetch_webpage, browser_manager

tool_registry.register_tool("web_search", web_search)
tool_registry.register_tool("fetch_webpage", fetch_webpage)
```

同时 `ToolRegistry.execute_tool` 需要支持 async 函数（现有实现只支持同步）。

### 5.5 `main.py` 改动

1. 应用启动时初始化浏览器：`await browser_manager.start(headless=True)`
2. 应用退出时关闭浏览器：`await browser_manager.stop()`
3. `config.ini` 增加 `[browser]` 配置段

### 5.6 `config.ini` 新增配置

```ini
[browser]
# 是否启用浏览器工具
enabled = true
# 是否使用无头模式（无界面）
headless = true
# 页面操作超时（秒）
timeout = 30
# 搜索引擎（bing/baidu/google）
search_engine = bing
# 抓取内容最大长度（字符）
max_content_length = 5000
# 单次工具调用超时（秒）
tool_timeout = 60
```

## 6. 异步工具适配

现有 `ToolRegistry.execute_tool` 是同步的，浏览器工具是 async。需改造：

```python
class ToolRegistry:
    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        tool_func = self.get_tool(tool_call["name"])
        if not tool_func:
            return ToolResult(..., error="工具不存在")

        try:
            if asyncio.iscoroutinefunction(tool_func):
                result = await asyncio.wait_for(
                    tool_func(**tool_call["arguments"]),
                    timeout=60  # 工具级超时保护
                )
            else:
                result = tool_func(**tool_call["arguments"])
            return ToolResult(..., result=result)
        except asyncio.TimeoutError:
            return ToolResult(..., error="工具执行超时")
        except Exception as e:
            return ToolResult(..., error=str(e))
```

`tool_dispatch_node` 也需改为 `async def`。

## 7. 安全与容错

| 风险 | 措施 |
|------|------|
| LLM 频繁调用浏览器导致资源耗尽 | 限制单轮对话最多 1 次工具调用循环（搜索→抓取→回答） |
| 页面加载超时 | Playwright `timeout=30s` + `asyncio.wait_for` 工具级 60s 超时 |
| 浏览器崩溃 | `BrowserManager` 检测连接状态，断开自动重启 |
| 恶意 URL | URL 协议白名单（仅 http/https），屏蔽内网地址 |
| 内容过长消耗 token | `max_length` 截断，默认 5000 字符 |
| 浏览器实例泄漏 | `try/finally` 确保 Context 关闭 |

## 8. 实现步骤

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 1 | 安装 Playwright + Chromium | 环境配置 |
| 2 | 实现 `browser_tool.py`（BrowserManager + web_search + fetch_webpage） | 新增 `tool/browser_tool.py` |
| 3 | 改造 `ToolRegistry` 支持 async + 超时 | `tool/tool_node.py` |
| 4 | 注册浏览器工具到 ToolRegistry | `tool/tool_node.py` |
| 5 | `chat_model.py` 增加 `tools` 参数和 `tool_calls` 解析 | `LLM/chat_model.py` |
| 6 | `LLM_node.py` 增加 tool_call 循环逻辑 | `LLM/LLM_node.py` |
| 7 | `main.py` 增加浏览器生命周期管理 | `main.py` |
| 8 | `config.ini` 增加 `[browser]` 配置段 | `config.ini` |
| 9 | 单元测试（web_search / fetch_webpage 独立测试） | `tool/test_browser_tool.py` |
| 10 | 集成测试（端到端：弹幕提问 → LLM 触发搜索 → 返回结果） | 手动验证 |

## 9. 测试方案

### 单元测试

```python
# tool/test_browser_tool.py
async def test_web_search():
    result = await web_search("今天北京天气", max_results=3)
    assert result["success"] is True
    assert len(result["results"]) > 0
    assert "title" in result["results"][0]

async def test_fetch_webpage():
    result = await fetch_webpage("https://example.com", extract_mode="text")
    assert result["success"] is True
    assert "content" in result
    assert len(result["content"]) <= 5000
```

### 集成测试

在直播间发送需要联网的问题，验证完整流程：

1. 弹幕："今天上海天气怎么样？" → LLM 调用 `web_search("上海天气")` → 返回天气信息
2. 弹幕："帮我查一下 Python 3.13 有什么新特性" → LLM 调用 `web_search` → 可能再调 `fetch_webpage` 抓取详情页 → 返回总结

## 10. 预期效果

用户弹幕提问需要联网的问题时，LLM 会自动：
1. 判断需要联网搜索
2. 调用 `web_search` 获取搜索结果
3. （可选）调用 `fetch_webpage` 抓取具体页面详情
4. 基于搜索/抓取的内容生成回复

整个过程对用户透明，用户只看到最终回复。
