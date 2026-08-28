# LLM_vtuber 优化方案（2026-08-28 更新）

> 基于当前项目实际状态重新规划，聚焦 3 大方向。
> 已完成项见文末「已实施清单」。

---

## 一、现状与基线

### 1.1 当前执行流程（已实施 2.1A 并行化改造后）

```
用户消息 / 弹幕
    ↓
init (初始化)
    ↓
load_memory (加载历史记忆)
    ↓
rag_retrieval (RAG 检索)
    ↓
context_control (上下文控制)
    ↓
llm_process (LLM 调用)          ← 主要瓶颈 2-5s
    ↓                           ← 最多 3 轮工具调用
finalize (立即返回)              ← 首字响应点
    ↓ (异步并行)
    ├→ rag_save (RAG 保存)
    ├→ save_memory (记忆保存)
    ├→ [live2d] (动作生成)
    └→ [tts / cloud_tts] (语音合成)
```

### 1.2 当前性能基线

| 指标 | 数值 |
|------|------|
| 首字响应时间 | 2-5s（取决于 LLM 调用耗时） |
| 完整响应时间 | 3-8s（含 TTS 合成） |
| Token 消耗 | 2000-3000（经 P0 优化后） |
| 工具调用率 | ~30%（每 10 次对话约 3 次触发工具） |
| 工具平均轮次 | 2.1 轮（MAX_TOOL_ROUNDS=3） |

### 1.3 已完成的优化

| 编号 | 优化项 | 效果 |
|------|--------|------|
| P0-1 | 精简系统提示词 | Token 节省 ~60% |
| P0-2 | 回复长度硬约束（100字） | 避免冗长回复 |
| P0-3 | 思考过程过滤 | reasoning_content 不再泄漏 |
| 2.1A | 后处理并行化 | 首字响应不再等待 TTS/存储 |
| — | 功能状态实例化 | 启停功能不重建图 |
| — | 停止时清空队列 | 停止后不再处理积压消息 |
| — | 仪表盘状态同步 | 弹幕/功能状态实时反映 |

---

## 二、优化方向一：Agent 效率与控制台响应速度

### 2.1 问题诊断

当前首字响应时间 2-5s 几乎全部消耗在 `llm_process` 节点：

| 环节 | 耗时 | 占比 |
|------|------|------|
| LLM API 调用 | 2000-5000ms | ~80% |
| RAG 检索 | 200-500ms | ~10% |
| 上下文控制 | 100-300ms | ~5% |
| 其他 | <100ms | ~5% |

**核心矛盾**：用户说一句话，80% 的时间在等 LLM 思考。

### 2.2 优化方案

#### 方案 2.2.1：工具调用智能短路

**现状**：`LLM_node.py` 中 `MAX_TOOL_ROUNDS = 3`，每次对话都允许 LLM 自由决定是否调用工具，导致：
- LLM 多花 1-2s 判断"要不要搜索"
- 搜索工具初始化（Playwright 启动浏览器）额外开销
- 搜索结果返回后还需再调用一次 LLM 总结

**优化**：引入"意图预判 + 确定性路由"，在 LLM 调用前做轻量规则判断：

```python
# LLM_node.py 新增
SEARCH_TRIGGER_KEYWORDS = ["今天", "最新", "最近", "现在", "实时", "当前", "新闻", "天气", "股价"]
SKIP_SEARCH_KEYWORDS = ["你好", "在吗", "自我介绍", "你是谁"]

def preprocess_intent(user_message: str) -> dict:
    """在 LLM 调用前预判意图，决定是否需要搜索"""
    msg = user_message.lower()
    
    # 明确跳过搜索
    if any(k in msg for k in SKIP_SEARCH_KEYWORDS):
        return {"need_search": False, "skip_tool": True}
    
    # 明确触发搜索
    if any(k in msg for k in SEARCH_TRIGGER_KEYWORDS):
        return {"need_search": True, "skip_tool": False}
    
    # 其他情况：降低工具轮次为 1
    return {"need_search": None, "skip_tool": False, "max_rounds": 1}
```

**效果**：~30% 的对话可完全跳过工具调用，首字响应时间从 2-5s 降至 1-3s。

#### 方案 2.2.2：搜索结果预摘要 + 注入

**现状**：搜索结果直接注入 LLM 上下文，一个关键词的搜索结果可能包含 500+ token 的无关内容。

**优化**：在工具层新增 `summarize_search_results`，将搜索结果压缩为 200 token 以内的结构化摘要：

```python
def summarize_search_results(results: list, max_tokens: int = 200) -> str:
    """将浏览器搜索结果压缩为 LLM 友好的摘要"""
    lines = []
    for r in results[:3]:
        title = r.get("title", "")[:30]
        snippet = r.get("snippet", "")[:80]
        lines.append(f"[{title}]: {snippet}")
    
    summary = "\n".join(lines)
    return _truncate_to_tokens(summary, max_tokens)
```

#### 方案 2.2.3：LLM 流式输出 + 逐句 TTS

**现状**：LLM 一次性返回完整回复 → 等 TTS 合成完毕 → 播放。

**优化**：LLM 使用流式 API，当输出积累到一句完整的话（检测到句号/感叹号）时，立即触发 TTS 合成并推送：

```
LLM Stream:  "你好呀~" → "今天看弹幕" → "好多人呀！"
                ↓              ↓              ↓
WebSocket:  立即推送        立即推送        立即推送
                ↓              ↓              ↓
TTS Chunk:  [合成1]        [合成2]        [合成3]
                ↓              ↓              ↓
Audio:      [播放1]        [播放2]        [播放3]
```

**预期效果**：首字响应时间从 2-5s 降至 500ms 以内，用户体验质变。

**实施要点**：
- 使用 `llm.astream()` 替代 `llm.invoke()`
- 以标点符号为分割点（`。！？~!?,.\n`）切片
- 切片内字数 ≥ 15 时才触发 TTS（避免过短音频）
- 已在 `docs/optimization_plan.md` 方案 2.5 中有详细设计，需适配当前代码结构

#### 方案 2.2.4：进程级浏览器实例复用

**现状**：`browser_tool.py` 中每次工具调用都启动新的 Playwright 浏览器实例，初始化耗时 ~2s。

**优化**：在 `ToolRegistry` 中维护单例浏览器实例，工具调用时复用：

```python
class ToolRegistry:
    def __init__(self):
        self._browser: Optional[Browser] = None
        self._playwright: Optional[AsyncPlaywright] = None
    
    async def get_browser(self) -> Browser:
        """懒加载单例浏览器"""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser
```

**预期效果**：工具调用延迟从 ~3s 降至 ~1s（节省浏览器启动时间）。

### 2.3 实施优先级

| 优先级 | 方案 | 预期收益 | 实施难度 | 依赖 |
|--------|------|---------|---------|------|
| **P0** | 2.2.1 工具调用智能短路 | 30% 对话提速 50% | 低 | 无 |
| **P0** | 2.2.2 搜索结果预摘要 | Token 节省 50% | 低 | 无 |
| **P1** | 2.2.4 浏览器实例复用 | 工具调用提速 60% | 中 | 需改 ToolRegistry |
| **P2** | 2.2.3 LLM 流式 + 逐句 TTS | 首字响应 <500ms | 高 | 需改 LLM_node + TTS + WebSocket |

---

## 三、优化方向二：知识与检索效率

### 3.1 问题诊断

当前 `RAG/knowledge_base.py` 和 `RAG/RAG_node.py` 存在以下效率问题：

| 问题 | 影响 |
|------|------|
| 每次查询都重新生成 embedding | 相同 query 重复消耗 embedding API |
| 无结果缓存 | 高频问题（如"你好"、"自我介绍"）重复检索 |
| 检索结果无排序优化 | 高分结果可能排在低分结果之后 |
| 搜索触发无判断 | 用户问"你是谁"也可能触发浏览器搜索 |
| 工具调用结果全量注入 | 搜索结果 500+ token 直接送入 LLM |

### 3.2 优化方案

#### 方案 3.2.1 查询语义缓存

在 `RAG_node.py` 中新增 LRU 缓存层，对相似问题直接复用历史检索结果：

```python
class CachedRAGRetriever:
    def __init__(self, max_size: int = 200, similarity_threshold: float = 0.92):
        self.cache: OrderedDict[str, tuple] = OrderedDict()  # query_hash → (embedding, results)
        self.max_size = max_size
        self.threshold = similarity_threshold
    
    async def retrieve(self, query: str, top_k: int = 5) -> list:
        """带语义缓存的检索"""
        query_embedding = await self._embed(query)
        
        # 查找缓存中最相似的 query
        for cached_query, (cached_emb, cached_results) in self.cache.items():
            similarity = cosine_similarity(query_embedding, cached_emb)
            if similarity >= self.threshold:
                logger.info(f"[RAG缓存] 命中: {cached_query[:30]} → {query[:30]}")
                # 更新 LRU
                self.cache.move_to_end(cached_query)
                return cached_results[:top_k]
        
        # 缓存未命中，执行实际检索
        results = await self._actual_retrieve(query, top_k)
        self.cache[hash(query)] = (query_embedding, results)
        self.cache.move_to_end(hash(query))
        
        # LRU 淘汰
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
        
        return results
```

**适用场景**：
- "你好" / "在吗" 等高频问候语
- "你叫什么名字" / "介绍一下自己" 等固定问答
- 相同话题的连续追问（"再说说天气" → "今天温度多少"）

**预期命中率**：≥ 30%（按当前使用场景估算）

#### 方案 3.2.2 检索结果智能过滤与排序

当前 `knowledge_base.py` 的 `search()` 方法返回原始结果，未做相关性优化。

优化点：
1. **分数阈值过滤**：低于 0.3 分的结果直接丢弃，避免噪音
2. **多样性排序**：优先返回不同 `content_type` 的结果（如同时返回"人物设定"+"历史对话"+"知识条目"）
3. **时效性加权**：最近 7 天内的内容加权提升（适用于直播间动态更新的知识）

```python
def rank_diversified_results(results: list, top_k: int = 5) -> list:
    """多样性重排序 + 时效性加权"""
    # 1. 分数阈值过滤
    results = [r for r in results if r.get("score", 0) >= 0.3]
    
    # 2. 按 content_type 分组
    by_type = defaultdict(list)
    for r in results:
        by_type[r.get("content_type", "unknown")].append(r)
    
    # 3. 交替选取，保证多样性
    diversified = []
    type_keys = list(by_type.keys())
    idx = 0
    while len(diversified) < top_k and any(by_type.values()):
        key = type_keys[idx % len(type_keys)]
        if by_type[key]:
            diversified.append(by_type[key].pop(0))
        idx += 1
    
    return diversified[:top_k]
```

#### 方案 3.2.3 工具调用前置过滤

在 `LLM_node.py` 的 `_run_doubao_chat` 中，**在 LLM 调用前** 做意图预判，减少不必要的工具调用：

```python
# 与 2.2.1 方案联动
def should_skip_search(user_message: str) -> bool:
    """判断是否应跳过浏览器搜索"""
    skip_patterns = [
        r"^(你好|在吗|hi|hello|嗨).*",
        r"^(你是谁|介绍一下|自我介绍).*",
        r".*(谢谢|感谢|多谢).*",
        r".*(再见|拜拜|下次).*",
    ]
    return any(re.match(p, user_message.lower()) for p in skip_patterns)

def should_force_search(user_message: str) -> bool:
    """判断是否必须触发搜索"""
    force_patterns = [
        r".*(今天|最新|最近|现在|实时|当前).*",
        r".*(新闻|天气|股价|汇率|比分).*",
        r".*(几号|日期|时间).*",
    ]
    return any(re.match(p, user_message.lower()) for p in force_patterns)
```

#### 方案 3.2.4 Embedding 懒加载与进程级复用

**现状**：`knowledge_base.py` 中 embedding 模型在每次 `_generate_embedding` 调用时检查并加载，但无跨实例共享。

**优化**：将 embedding 模型提升为模块级单例，启动时懒加载一次：

```python
# knowledge_base.py 模块级
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = load_embedding_model()  # 首次调用时加载
    return _embedding_model
```

### 3.3 实施优先级

| 优先级 | 方案 | 预期收益 | 实施难度 |
|--------|------|---------|---------|
| **P0** | 3.2.3 工具调用前置过滤 | 减少 30-50% 无效搜索 | 低 |
| **P1** | 3.2.1 查询语义缓存 | 30%+ 查询 0 延迟 | 中 |
| **P1** | 3.2.2 多样性重排序 | 检索质量提升 | 低 |
| **P2** | 3.2.4 Embedding 单例 | 减少重复加载开销 | 低 |

---

## 四、优化方向三：LLM 电脑操作能力

### 4.1 功能概述

为大模型新增「操作电脑」的能力，使其能够：
- 读取屏幕内容（截图 + OCR）
- 操作鼠标（点击、拖拽、滚动）
- 操作键盘（输入文本、快捷键）
- 管理窗口（打开、关闭、切换）
- 操作文件（读取、写入、搜索）

**应用场景**：
- 帮用户打开浏览器搜索信息并总结
- 帮用户整理文件、重命名
- 帮用户操作常用软件（记事本、计算器等）
- 直播辅助：自动切换场景、启动/停止录制

### 4.2 系统架构

```
                    ┌─────────────────────┐
                    │   LLM (豆包 API)    │
                    │   function calling  │
                    └─────────┬───────────┘
                              │ tool_calls
                              ▼
                    ┌─────────────────────┐
                    │   ToolRegistry      │
                    │  (统一工具路由)      │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ ScreenTool   │  │ MouseTool    │  │ KeyboardTool │
    │  截图/OCR    │  │ 点击/拖拽    │  │ 输入/快捷键  │
    └──────────────┘  └──────────────┘  └──────────────┘
              │               │               │
              ▼               ▼               ▼
    ┌──────────────────────────────────────────────────┐
    │              Windows 系统 API                     │
    │  (pyautogui / win32api / pygetwindow / uiautomator)│
    └──────────────────────────────────────────────────┘
```

### 4.3 工具定义

#### 4.3.1 ScreenTool（屏幕感知）

```python
# tool/screen_tool.py
from pydantic import BaseModel, Field

class ScreenCapture(BaseModel):
    """截取屏幕内容供 LLM 分析"""
    region: str = Field(default="full", description="区域: full/active_window/指定坐标")
    ocr: bool = Field(default=True, description="是否进行 OCR 识别")
    description: str = Field(default="", description="想要了解的内容描述（辅助 OCR 聚焦）")

class ScreenTool:
    name = "screen_capture"
    description = "截取当前屏幕或指定区域的画面，进行OCR识别，返回屏幕上的文字和界面元素"
    
    async def execute(self, args: dict) -> dict:
        # 1. 使用 pyautogui 截图
        # 2. 调用 OCR 服务（本地 Tesseract 或云端 API）
        # 3. 返回文字内容和元素位置
        pass
```

#### 4.3.2 MouseTool（鼠标操作）

```python
class MouseClick(BaseModel):
    x: int = Field(description="点击 X 坐标")
    y: int = Field(description="点击 Y 坐标")
    button: str = Field(default="left", description="left/right/middle")
    clicks: int = Field(default=1, description="点击次数")

class MouseDrag(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    duration: float = Field(default=0.5, description="拖拽持续时间（秒）")

class MouseTool:
    name = "mouse_action"
    description = "控制鼠标进行点击、拖拽、滚动等操作"
    
    async def execute(self, args: dict) -> dict:
        # 支持: click, double_click, right_click, drag, scroll
        pass
```

#### 4.3.3 KeyboardTool（键盘操作）

```python
class KeyboardType(BaseModel):
    text: str = Field(description="要输入的文本")
    interval: float = Field(default=0.05, description="按键间隔（秒）")

class KeyboardShortcut(BaseModel):
    keys: list[str] = Field(description="组合键，如 ['ctrl', 'c']")

class KeyboardTool:
    name = "keyboard_action"
    description = "控制键盘进行文本输入、快捷键操作"
    
    async def execute(self, args: dict) -> dict:
        # 支持: type_text, press_key, hotkey
        pass
```

#### 4.3.4 WindowTool（窗口管理）

```python
class WindowAction(BaseModel):
    action: str = Field(description="open/close/activate/minimize/maximize")
    window_name: str = Field(description="窗口标题或应用名称")
    path: str = Field(default="", description="open 时的应用路径")

class WindowTool:
    name = "window_action"
    description = "管理 Windows 窗口：打开、关闭、激活、最小化、最大化"
    
    async def execute(self, args: dict) -> dict:
        # 使用 pygetwindow 或 win32gui
        pass
```

### 4.4 安全防护机制

电脑操作能力风险较高，必须设计安全防护：

#### 4.4.1 操作确认层

```python
class SafetyGuard:
    """操作安全守卫"""
    
    DANGEROUS_ACTIONS = [
        "delete_file", "format_drive", "shutdown", "restart",
        "mouse_click_unauthorized", "keyboard_execute_command"
    ]
    
    async def check_and_confirm(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """检查操作是否安全，危险操作需用户确认"""
        
        # 1. 检查工具是否在白名单
        if tool_name not in self.whitelist:
            return False, f"工具 {tool_name} 不在允许列表中"
        
        # 2. 检查是否为危险操作
        if self._is_dangerous(tool_name, args):
            # 需用户通过 WebUI 确认
            await self._request_user_confirmation(tool_name, args)
            return True, "等待用户确认"
        
        # 3. 检查操作频率
        if not self._check_rate_limit(tool_name):
            return False, "操作过于频繁，请稍后再试"
        
        return True, "操作允许"
```

#### 4.4.2 操作日志与可追溯

```python
class OperationLogger:
    """操作日志记录器"""
    
    async def log_operation(self, tool_name: str, args: dict, result: dict):
        """记录每次电脑操作"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "args": args,
            "result": result,
            "screenshot": await self._capture_before_after(),  # 操作前后截图
        }
        await self._save_to_audit_log(entry)
```

#### 4.4.3 沙箱模式

```python
class SandboxMode:
    """沙箱模式 - 限制操作范围"""
    
    ALLOWED_APPS = ["notepad", "calc", "browser", "explorer"]
    ALLOWED_PATHS = ["C:/Users/*/Documents", "C:/Users/*/Desktop"]
    
    def is_sandboxed(self, tool_name: str, args: dict) -> bool:
        """检查操作是否在沙箱范围内"""
        if tool_name == "window_action" and args.get("action") == "open":
            return args.get("window_name", "").lower() in self.ALLOWED_APPS
        if tool_name == "file_action":
            return any(p in args.get("path", "") for p in self.ALLOWED_PATHS)
        return True  # 默认允许安全操作
```

### 4.5 实施计划

| 阶段 | 内容 | 预估工期 | 安全策略 |
|------|------|---------|---------|
| **阶段 1** | ScreenTool + WindowTool | 2天 | 仅允许读取和窗口管理，无破坏性操作 |
| **阶段 2** | MouseTool + KeyboardTool | 2天 | 仅允许在沙箱内操作，危险操作需确认 |
| **阶段 3** | 安全防护层 + 操作日志 | 1天 | 完整的审计日志和用户确认机制 |
| **阶段 4** | 集成到 LLM 工具调用链 | 1天 | 在 `ToolRegistry` 中注册，与浏览器工具统一调度 |

**依赖**：
- `pyautogui`（鼠标键盘控制）
- `pygetwindow` / `win32gui`（窗口管理）
- `pytesseract` 或 PaddleOCR（OCR 识别）
- `mss`（高性能截图，比 PIL 快 3-5 倍）

### 4.6 LLM 工具注册示例

```python
# LLM_node.py 中注册新工具
def _get_computer_tools():
    """获取电脑操作工具 Schema"""
    return [
        {
            "type": "function",
            "function": {
                "name": "screen_capture",
                "description": "截取屏幕画面并OCR识别文字内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string", "description": "full/active_window/坐标"},
                        "focus": {"type": "string", "description": "要关注的内容"}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "mouse_action",
                "description": "控制鼠标进行点击或拖拽",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "click/drag/scroll"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"}
                    },
                    "required": ["action"]
                }
            }
        },
        # ... keyboard_action, window_action
    ]
```

### 4.7 示例对话

**用户**：帮我打开记事本，输入今天的直播计划

**LLM 工具调用序列**：
1. `window_action(action="open", window_name="notepad")` → 打开记事本
2. `screen_capture(region="active_window")` → 确认记事本已打开
3. `keyboard_action(action="type", text="2026-08-28 直播计划\n1. 弹幕互动\n2. 游戏环节")` → 输入文本
4. `window_action(action="close", window_name="notepad")` → [需用户确认] 不执行

**用户**：帮我看看屏幕上有什么

**LLM 工具调用序列**：
1. `screen_capture(region="full", focus="屏幕上的窗口和文字")` → 截图 + OCR
2. LLM 基于 OCR 结果生成描述："屏幕上有 3 个窗口：浏览器、记事本、终端..."

---

## 五、总体实施路线图

### 阶段 1：Agent 效率提升（1-3天）

| 序号 | 任务 | 方向 | 产出 |
|------|------|------|------|
| 1.1 | 工具调用智能短路（2.2.1） | 方向一 | 意图预判 + 跳过/强制搜索 |
| 1.2 | 搜索结果预摘要（2.2.2） | 方向一 | 200 token 摘要 |
| 1.3 | 浏览器实例复用（2.2.4） | 方向一 | 单例 Playwright |
| 1.4 | 工具调用前置过滤（3.2.3） | 方向二 | 正则模式匹配 |

### 阶段 2：检索效率优化（2-4天）

| 序号 | 任务 | 方向 | 产出 |
|------|------|------|------|
| 2.1 | 查询语义缓存（3.2.1） | 方向二 | LRU + 余弦相似度 |
| 2.2 | 多样性重排序（3.2.2） | 方向二 | 交替选取算法 |
| 2.3 | Embedding 单例（3.2.4） | 方向二 | 模块级共享 |
| 2.4 | 统计缓存命中率 | 方向二 | 可观测性指标 |

### 阶段 3：电脑操作能力（4-6天）

| 序号 | 任务 | 方向 | 产出 |
|------|------|------|------|
| 3.1 | ScreenTool + WindowTool | 方向三 | 屏幕感知 + 窗口管理 |
| 3.2 | MouseTool + KeyboardTool | 方向三 | 鼠标键盘控制 |
| 3.3 | 安全防护层 | 方向三 | 操作确认 + 日志 + 沙箱 |
| 3.4 | 集成到 LLM 工具链 | 方向三 | 在 ToolRegistry 注册 |

### 阶段 4：流式体验（5-7天，可选）

| 序号 | 任务 | 方向 | 产出 |
|------|------|------|------|
| 4.1 | LLM 流式输出 | 方向一 | astream + 标点切片 |
| 4.2 | 逐句 TTS 合成 | 方向一 | 流式 TTS 触发 |
| 4.3 | WebSocket 流式推送 | 方向一 | 前端实时渲染 |

---

## 六、关键指标与验收标准

### 6.1 方向一：Agent 效率

| 指标 | 当前 | 阶段1目标 | 阶段4目标 |
|------|------|----------|----------|
| 首字响应时间 | 2-5s | 1-3s | <500ms |
| 完整响应时间 | 3-8s | 2-5s | 1-2s |
| Token 消耗 | 2000-3000 | 1500-2500 | 1000-1500 |
| 工具平均轮次 | 2.1 | 1.2 | 1.0 |
| 浏览器工具启动耗时 | ~2s | <0.5s | <0.5s |

### 6.2 方向二：检索效率

| 指标 | 当前 | 阶段2目标 |
|------|------|----------|
| 缓存命中率 | 0% | ≥ 30% |
| 检索平均耗时 | 200-500ms | <100ms（缓存命中） |
| 检索结果相关性 | 原始排序 | 多样性重排 |
| 无效搜索率 | ~30% | <10% |

### 6.3 方向三：电脑操作

| 指标 | 验收标准 |
|------|----------|
| 屏幕截取准确率 | OCR 文字识别 ≥ 95% |
| 鼠标定位精度 | 坐标误差 ≤ 5 像素 |
| 键盘输入准确率 | 100%（特殊键无遗漏） |
| 安全防护覆盖 | 所有危险操作均需确认 |
| 操作日志完整性 | 100% 操作有审计记录 |

---

## 七、风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 意图预判误判 | 该搜索时未搜索 | 预判仅作为"降低轮次"而非完全跳过，LLM 仍可自主决定 |
| 语义缓存过期 | 返回过时信息 | 实时性敏感问题（含"今天""最新"）强制跳过缓存 |
| 浏览器实例崩溃 | 工具调用失败 | 实现健康检查，异常时自动重建实例 |
| 电脑操作误操作 | 执行了危险操作 | 危险操作需用户确认 + 沙箱模式限制范围 |
| OCR 识别不准 | LLM 理解错误 | 返回置信度，低置信度时提示用户确认 |
| 流式传输丢包 | 音频/文字断续 | 实现缓冲队列和重传机制 |

---

## 八、已实施清单（截至 2026-08-28）

| 编号 | 项目 | 文件 | 状态 |
|------|------|------|------|
| P0-1 | 精简系统提示词 | `main.py` | ✅ 已实施 |
| P0-2 | 回复长度硬约束（100字） | `main.py` | ✅ 已实施 |
| P0-3 | 思考过程过滤 | `LLM/chat_model.py` | ✅ 已实施 |
| 2.1A | 后处理并行化改造 | `main.py` | ✅ 已实施 |
| — | 功能状态实例化 | `main.py` | ✅ 已实施 |
| — | 停止时清空消息队列 | `main.py` | ✅ 已实施 |
| — | 仪表盘状态同步 | `webui/app.py`, `webui/index.html` | ✅ 已实施 |
| — | 配置持久化 | `config.ini` | ✅ 已实施 |
