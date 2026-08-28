# LLM_vtuber 自主网页搜索功能 - Product Requirement Document

## Overview
- **Summary**: 为 LLM_vtuber 项目添加自主网页搜索能力，当 AI 遇到不知道或不确定的内容时，能够主动调用浏览器工具搜索网页获取信息，从而提供更准确、更有时效性的回答。
- **Purpose**: 解决 AI 知识截止日期限制和无法获取实时信息的问题，提升虚拟主播的信息检索能力和回答质量。
- **Target Users**: 直播间观众、与虚拟主播互动的用户。

## Goals
- **G1**: AI 能够自主判断何时需要搜索信息（遇到不确定的内容、需要最新信息时）
- **G2**: AI 能够执行完整的搜索流程：搜索 → 阅读 → 整合回答
- **G3**: 支持多轮工具调用循环（搜索后可继续搜索或获取更多详情）
- **G4**: 搜索过程对用户透明，AI 会在回答中自然地融入搜索到的信息
- **G5**: 控制搜索频率和超时，避免无限循环或长时间等待

## Non-Goals (Out of Scope)
- 不实现复杂的 Agent 规划系统（如 ReAct、Plan-and-Execute）
- 不添加新的搜索引擎支持（仅使用现有的 Bing/Baidu）
- 不实现搜索结果的长期缓存或知识更新
- 不支持用户手动触发搜索功能
- 不修改 Live2D 动作相关逻辑

## Background & Context
- 项目已有完整的浏览器工具链：`browser_tool.py` 提供 `web_search` 和 `fetch_webpage` 两个工具
- 工具注册表 `tool_node.py` 已将浏览器工具注册到系统中
- LLM 节点 `LLM_node.py` 支持工具调用循环，但目前仅支持一轮（tool_calls → 执行 → 最终回复）
- 系统提示词（`SYSTEM_PROMPT_LIVE2D` / `SYSTEM_PROMPT_DEFAULT`）中没有工具使用指引
- 项目使用豆包（Doubao）大模型，支持 function calling 能力

## Functional Requirements

### FR-1: 智能搜索触发
- 当用户询问实时信息（新闻、天气、股票价格等）时，AI 应主动搜索
- 当 AI 对回答不确定或没有相关知识时，应主动搜索
- 当用户询问需要具体数据或事实支撑的问题时，应主动搜索

### FR-2: 多轮工具调用
- LLM 节点支持最多 3 轮工具调用循环
- 每轮可以执行多个工具调用（如同时搜索多个关键词）
- 工具执行结果会反馈给 LLM，LLM 可决定继续搜索或生成最终回答

### FR-3: 搜索工具使用指引
- 系统提示词中包含工具使用说明
- 明确告诉 AI 何时应该使用搜索工具
- 提供搜索策略建议（如先搜索关键词，再获取详情页内容）

### FR-4: 搜索结果整合
- AI 将搜索到的信息自然融入回答中
- 回答风格保持虚拟主播的人设（活泼、可爱、口语化）
- 对于 Live2D 模式，仍需遵循【语气】内容|目光|嘴巴 格式

### FR-5: 安全与限制
- 单次对话最多 3 轮工具调用，防止无限循环
- 单次工具调用超时 60 秒，防止卡死
- 搜索失败时降级为直接回答（诚实告知不知道）

## Non-Functional Requirements

### NFR-1: 性能
- 单次搜索流程（搜索+获取详情）不超过 15 秒
- 工具调用总超时不超过 180 秒（3轮 × 60秒）

### NFR-2: 可靠性
- 搜索失败时不影响对话流程，AI 应优雅降级
- 浏览器实例异常时能自动恢复

### NFR-3: 可维护性
- 工具调用轮数可通过配置调整
- 搜索触发逻辑可通过提示词优化调整

## Constraints
- **Technical**: 
  - 依赖 Playwright 浏览器（已安装）
  - 依赖豆包大模型的 function calling 能力
  - 浏览器工具已在 `tool/browser_tool.py` 中实现
- **Dependencies**:
  - `browser_tool.py`: web_search, fetch_webpage 函数
  - `tool_node.py`: 工具注册表和执行框架
  - `LLM_node.py`: LLM 调用和工具循环逻辑

## Assumptions
- 豆包模型支持多轮 function calling（已验证支持）
- 浏览器工具有足够的稳定性处理搜索请求
- 用户能接受 AI 偶发的搜索延迟（5-15秒）
- 搜索到的信息质量足够支撑 AI 回答

## Acceptance Criteria

### AC-1: 搜索触发能力
- **Given**: 用户询问实时信息（如"今天B站有什么热门"、"最新的AI新闻"）
- **When**: AI 处理用户问题
- **Then**: AI 主动调用 `web_search` 工具搜索相关内容
- **Verification**: `programmatic`
- **Notes**: 通过日志检测是否有工具调用记录

### AC-2: 多轮工具调用
- **Given**: AI 需要先搜索关键词再获取详情
- **When**: 第一轮搜索结果不足以回答问题
- **Then**: AI 可以继续发起第二轮工具调用（如调用 `fetch_webpage` 获取详情页）
- **Verification**: `programmatic`
- **Notes**: 检查日志中有连续的工具调用记录

### AC-3: 搜索结果整合
- **Given**: AI 已获取搜索结果
- **When**: AI 生成最终回答
- **Then**: 回答中包含搜索到的信息，且风格符合虚拟主人设
- **Verification**: `human-judgment`
- **Notes**: 人工评估回答的自然度和信息准确性

### AC-4: 降级处理
- **Given**: 搜索工具调用失败或超时
- **When**: AI 收到工具执行失败的反馈
- **Then**: AI 诚实地告知用户无法获取该信息，并给出一般性回答
- **Verification**: `programmatic`
- **Notes**: 检查错误处理流程

### AC-5: 循环限制
- **Given**: AI 连续发起工具调用
- **When**: 工具调用轮数达到 3 轮上限
- **Then**: AI 必须基于已有信息生成最终回答，不再继续搜索
- **Verification**: `programmatic`
- **Notes**: 检查代码中的轮数计数器逻辑

### AC-6: 系统提示词包含工具指引
- **Given**: AI 的系统提示词
- **When**: 检查提示词内容
- **Then**: 提示词中明确包含工具使用说明和搜索触发场景
- **Verification**: `programmatic`
- **Notes**: 验证 SYSTEM_PROMPT 常量字符串

## Open Questions
- [ ] 是否需要在 WebUI 中显示 AI 的搜索行为（如"正在搜索..."的状态提示）？
- [ ] 是否需要记录 AI 的搜索历史用于后续分析？
- [ ] 搜索结果是否需要经过安全过滤（防止 AI 获取不良内容）？
