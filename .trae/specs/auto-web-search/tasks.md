# LLM_vtuber 自主网页搜索功能 - The Implementation Plan

## [x] Task 1: 更新系统提示词，添加工具使用指引
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 修改 `main.py` 中的 `SYSTEM_PROMPT_LIVE2D` 和 `SYSTEM_PROMPT_DEFAULT` 常量
  - 添加工具使用说明章节，明确告知 AI 可用的工具（web_search, fetch_webpage）
  - 添加搜索触发场景说明，指导 AI 在何时应该主动搜索
  - 添加搜索策略建议（先搜索关键词 → 再获取详情 → 整合回答）
  - 示例提示："当用户询问实时信息、最新资讯或你不确定的内容时，使用 web_search 工具搜索"
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-1.1: ✓ SYSTEM_PROMPT_LIVE2D 和 SYSTEM_PROMPT_DEFAULT 中包含 "web_search" 和 "fetch_webpage" 关键字
  - `programmatic` TR-1.2: ✓ 提示词中包含搜索触发场景描述（"不确定"、"实时信息"、"最新"等）
  - `human-judgement` TR-1.3: ✓ 人工审阅提示词内容，指引清晰易懂
- **Notes**: 提示词风格与现有虚拟主人设保持一致

## [x] Task 2: 实现多轮工具调用循环
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 修改 `LLM/LLM_node.py` 中的 `_run_doubao_chat` 函数
  - 添加工具调用轮数计数器（max_tool_rounds = 3）
  - 将当前的"一轮工具调用"改为"循环调用"
  - 添加日志记录每轮工具调用的情况
- **Acceptance Criteria Addressed**: AC-2, AC-5
- **Test Requirements**:
  - `programmatic` TR-2.1: ✓ 代码审查确认存在 while 循环
  - `programmatic` TR-2.2: ✓ 代码审查确认轮数计数器在循环内递增
  - `programmatic` TR-2.3: ✓ 代码审查确认达到轮数上限后使用 `tools=None` 生成最终回答
  - `programmatic` TR-2.4: ✓ 测试脚本验证轮数限制逻辑正确
- **Notes**: 保留了现有的单轮兼容逻辑

## [x] Task 3: 添加搜索降级处理逻辑
- **Priority**: high
- **Depends On**: Task 2
- **Description**: 
  - 在 `_execute_tool_calls` 函数中增强错误处理
  - 工具执行失败时，返回结构化的错误结果
  - 单个工具失败不阻塞其他工具执行
  - 添加超时保护（60 秒）
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-3.1: ✓ 代码审查确认 `_execute_tool_calls` 中存在 try-except 处理
  - `programmatic` TR-3.2: ✓ 代码审查确认超时使用 `asyncio.wait_for`
  - `programmatic` TR-3.3: ✓ 测试脚本验证工具失败场景不崩溃
- **Notes**: 降级处理不影响正常工具调用的性能

## [x] Task 4: 添加上下文搜索状态标记
- **Priority**: medium
- **Depends On**: Task 2
- **Description**: 
  - 在 `LLMState` 中添加搜索状态字段
  - 在 `_run_doubao_chat` 中更新这些状态字段
- **Acceptance Criteria Addressed**: AC-1 (辅助)
- **Test Requirements**:
  - `programmatic` TR-4.1: ✓ 代码审查确认 LLMState 类型定义中包含新字段
  - `programmatic` TR-4.2: ✓ 代码审查确认工具循环中正确更新状态字段
  - `human-judgement` TR-4.3: ✓ 搜索状态标记便于调试和监控
- **Notes**: 可选增强，不影响核心搜索功能

## [x] Task 5: 集成测试与验证
- **Priority**: high
- **Depends On**: Task 1, Task 2, Task 3
- **Description**: 
  - 编写单元测试脚本 `test_auto_search.py`
  - 验证所有核心逻辑
  - 测试通过率 100%
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-4, AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-5.1: ✓ 测试脚本验证系统提示词包含工具指引
  - `programmatic` TR-5.2: ✓ 测试脚本验证多轮工具调用逻辑正确
  - `programmatic` TR-5.3: ✓ 测试脚本验证轮数上限限制
  - `programmatic` TR-5.4: ✓ 测试脚本验证搜索失败时的降级处理
  - `human-judgement` TR-5.5: ✓ 代码质量和可维护性良好
- **Notes**: 端到端集成测试需要实际 LLM API 和浏览器环境
