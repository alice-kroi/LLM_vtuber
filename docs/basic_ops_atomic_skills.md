# 基础操作原子化技能设计

> 文档日期：2026-09-19
> 对应代码：`tool/computer_use.py`、`tool/tool_node.py`、`LLM/operator_agent.py`
> 设计对齐：可提（Pal-AI-Lab/Cortico）的技能原语库 + mc_check 验证闭环思路

## 1. 背景与目标

把 Windows 电脑操作抽象成一组**原子技能**，让 LLM（主播脑/操作脑）通过"技能名 + 参数"组合完成任务，而不是发明操作方式。

三层结构：

```
原子技能库（A/B/C/D/E/F 六域）  ← 单一职责、参数化、可验证
  ↓ 组合
组合模板（高频流程预置）        ← 找文件并打开 / 浏览器查资料 / 标准控件操作…
  ↓ 编排
三脑分工（主播直操信息类，操作脑白名单执行操作类，主体只路由）
```

## 2. 设计原则

1. **原子性**：一个技能 = 一个不可再分的系统操作，单一职责。复合操作（定位+点击+校验）显式标注为"复合技能"。
2. **可验证**：除只读定位技能外，每个技能有配套 `os_check` 断言（执行 → 验证 → 修正）。
3. **白名单隔离**：操作脑只碰操作类白名单（`OPERATOR_TOOL_NAMES`）；主播直操信息类，防越权与双执行。
4. **失败可观测**：回执统一 `{success, result, evidence, error}`，差异驱动修正。
5. **组合而非堆叠**：多步任务交给操作脑 ReAct 组步，或使用预置组合模板。

## 3. 技能目录（六域）

### A 定位域（只读）

| 技能 | 触发场景 | 验证 |
|---|---|---|
| `os_list_windows` | 打开哪些窗口 / 找某窗口 | — |
| `os_list_directory` | 某文件夹里有什么 | — |
| `os_find_file(keyword, root, depth, limit)` | XX 文件在哪里 | `count > 0` 即命中 |
| `ui_list_elements(window_title, limit)` | 界面控件名与真实坐标 | — |
| `os_capture_window(window_title)` | 看看某窗口里是什么（PrintWindow 截窗+视觉描述） | — |

### B 打开域（系统 Shell，最稳）

| 技能 | 触发场景 | 验证 |
|---|---|---|
| `os_open_path(path)` | 打开盘符 / 文件夹 / URL / 应用 | `os_check(window)` |
| `os_open_file(path)` | 打开具体文件（默认程序） | `os_check(window)` |

### C 操作域（键鼠）

| 技能 | 触发场景 | 验证 |
|---|---|---|
| `os_click_xy(x, y)` | 绝对坐标点击 | `os_check(screen/element)` |
| `os_type_text(text)` | 输入文本（中文走剪贴板） | `os_check(screen)` |
| `os_press_key(hotkey)` | 组合键 / 回车 | — |
| `os_scroll(dy)` | 滚动页面 | — |
| `vision_click(window_title, description)` | **复合**：描述目标→视觉定位→点击→截图校验→修正 | 自带校验 |
| `os_activate_window(title)` | 激活窗口 | `os_check(window)` |
| `os_minimize_window(title)` / `os_show_desktop()` | 清理遮挡 | `os_check(window)` |

### D 验证域

| 技能 | 触发场景 |
|---|---|
| `os_check(check_type, target, expect)` | 核验窗口/文件/目录/UI 控件/画面断言，回执只报差异 |

`os_check` 是闭环的核心：`window` / `file` / `dir` / `element('窗口>>控件名')` / `screen('窗口>>画面描述')` 五类断言，支持 `expect="false"` 反转。

### E 浏览器域

| 技能 | 触发场景 | 验证 |
|---|---|---|
| `os_browser_search(keyword, engine)` | **复合**：拼搜索 URL→受控 Edge 打开→返回快照；Edge 不可用回退默认浏览器 | 返回 `next_view` |

### F 信息域（主播直操，不在操作脑白名单）

`web_search` / `browser_navigate/click/type/press_key/scroll/tabs` / `vision_analyze` / `tts_synthesize`

## 4. 技能卡规范（SKILL_DOC 等价物）

每个技能一张卡，统一格式：

```
技能: os_find_file
一句话: 按文件名关键词递归搜索文件位置
触发: 用户问"XX 文件在哪里 / 帮我找 XX 文件"
参数: keyword(必) root(默认C:\) depth(默认6) limit(默认20)
副作用: 只读，无
验证: count>0 即命中；未命中 → 换 root 缩小或换关键词
组合: 找到后接 os_open_file → os_check(window) 确认打开
```

示例二（操作脑最常用组合）：

```
技能: os_check
一句话: 断言式核验，回执只报差异
触发: 每次执行操作后必须验证结果；os_check 失败回执含差异原因，据此修正
参数: check_type(window/file/dir/element/screen) target expect(true/false)
验证: 无（自身即验证）
组合: 与所有 B/C/E 域技能搭配，形成"执行→验证→修正"闭环
```

## 5. 原子 vs 复合

- **纯原子**：A 域、B 域、C 域中除 vision_click 外全部、D 域。
- **复合技能**（内部含多步，对 LLM 仍是一次调用）：
  - `vision_click`：GUI 定位 + 点击 + 截图校验 + 限次修正
  - `os_browser_search`：URL 拼装 + EdgeAgent 导航 + 快照 + 默认浏览器回退
  - `os_capture_window`：PrintWindow 截窗 + 保存 + 视觉模型描述

## 6. 组合模板（高频流程预置）

| 模板 | 技能序列 | 典型场景 |
|---|---|---|
| 找文件并打开 | `os_find_file` → `os_open_file` → `os_check(window)` | "打开我的计划文档" |
| 浏览器查资料 | `os_browser_search` → 读 `next_view` → 需要时 `browser_click/type` | "帮我搜一下 XX" |
| 打开目录看内容 | `os_open_path` → `os_list_directory` → 需要时 `os_open_file` | "E盘有什么" |
| 标准控件操作 | `ui_list_elements` 拿坐标 → `os_click_xy` → `os_check(element)` | "点保存按钮" |
| 非标准界面操作 | `os_capture_window` 看现状 → `vision_click` 定位点击 → `os_check(screen)` | webview / 游戏界面 |
| 桌面清理 | `os_list_windows` 盘点 → `os_minimize_window` / `os_show_desktop` → 再定位目标 | 目标被遮挡 |

## 7. 注册与暴露

现状已支持，无需新基建：

- 操作脑白名单 `OPERATOR_TOOL_NAMES` = A + B + C + D + E 域（17 个）
- 主播 `_get_all_tools()` = 全量（F 域 + 操作类兜底）
- 所有工具经 `tool_registry.execute_tool` 统一执行（`tool/tool_node.py`）
- Schema 全部声明在 `COMPUTER_USE_TOOLS_SCHEMA`（`tool/computer_use.py`）

## 8. 与三脑架构衔接

```
主播脑 streamer —— 直操 F 域（信息查询/表达）
   │ 委托（streamer_operation_request）
主体 supervisor —— 只路由 + 维护 desktop_snapshot（唯一工具：os_list_windows）
   │ 指令 / 提交 TaskManager
操作脑 operator —— ReAct 循环，白名单执行 A~E 域，实时 task_update 上报
```

防双执行：主播末轮 `effective_tools=None`；操作意图优先走委托链，主播仅在"看"类等未命中关键词时兜底直操。

## 9. 后续扩展（P1+）

1. **SKILL_DOC 聚合注入**：把第 4 节技能卡汇总成一张聚合文档注入操作脑 system prompt，比零散 tool description 更能约束"执行→验证→修正"行为。
2. **意图裁剪**：按 `computer_intent` 命中只暴露 A~E 域子集，减少无效工具触发。
3. **操作经验账本**（对齐可提 chests/explored/deaths）：记录应用启动方式、稳定窗口标题、已验证坐标，跨局复用减少试错。
4. **操作日志**：组合模板执行轨迹入库，供复盘与优化。

## 10. 与可提（Cortico）对照

| 可提 | 本项目对应 |
|---|---|
| skills.ts 技能原语库（40+） | `tool/computer_use.py` os_* 原子工具（17 操作类 + 信息类） |
| mc_do(steps) 组步 | 操作脑 ReAct 循环 + TaskManager |
| mc_check 断言验证 | `os_check`（window/file/dir/element/screen） |
| 反射层（不经 LLM 自保） | 电脑操作场景弱，用超时 + 失败兜底替代 |
| 账本记忆（chests/explored/deaths） | P1：操作经验账本 |
| ADAPT.md 版本适配手册 | P1：操作兼容性记录 |
