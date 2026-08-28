# LLM_vtuber 自主网页搜索功能 - Verification Checklist

## 系统提示词验证
- [x] Checkpoint 1: `SYSTEM_PROMPT_LIVE2D` 中包含工具使用说明，提及 `web_search` 和 `fetch_webpage`
- [x] Checkpoint 2: `SYSTEM_PROMPT_DEFAULT` 中包含工具使用说明，提及 `web_search` 和 `fetch_webpage`
- [x] Checkpoint 3: 提示词中包含搜索触发场景描述（如"不确定"、"实时信息"、"最新资讯"等关键词）
- [x] Checkpoint 4: 提示词中包含搜索策略建议（先搜索关键词，再获取详情）

## 多轮工具调用验证
- [x] Checkpoint 5: `_run_doubao_chat` 函数中存在 while 循环用于多轮工具调用
- [x] Checkpoint 6: 工具调用轮数计数器正确实现，上限为 3 轮（MAX_TOOL_ROUNDS = 3）
- [x] Checkpoint 7: 每轮工具执行后，结果正确加入消息历史（包含 assistant tool_calls 和 tool 结果消息）
- [x] Checkpoint 8: 达到轮数上限后，使用 `tools=None` 调用 LLM 生成最终回答
- [x] Checkpoint 9: 无工具调用时（tool_calls 为空），直接生成最终回答，不进入循环

## 降级处理验证
- [x] Checkpoint 10: `_execute_tool_calls` 函数中存在 try-except 错误处理
- [x] Checkpoint 11: 工具执行失败时返回结构化错误结果（包含 error 字段）
- [x] Checkpoint 12: 单个工具失败不阻塞同批次其他工具的执行
- [x] Checkpoint 13: 工具执行存在超时保护（60 秒）
- [x] Checkpoint 14: 所有工具失败时，LLM 基于失败反馈生成诚实回答

## 搜索状态标记验证
- [x] Checkpoint 15: LLMState 中包含 `search_performed`, `search_results_count`, `search_rounds` 字段
- [x] Checkpoint 16: 工具循环中正确更新搜索状态字段
- [x] Checkpoint 17: 搜索状态字段有合理的默认值（未搜索时为 False/0）

## 集成测试验证（单元测试通过）
- [x] Checkpoint 18: 测试脚本验证系统提示词包含工具指引（test 5 通过）
- [x] Checkpoint 19: 测试脚本验证多轮工具调用逻辑正确（test 6 通过）
- [x] Checkpoint 20: 测试脚本验证轮数上限限制（test 6 场景 4 通过）
- [x] Checkpoint 21: 测试脚本验证搜索失败降级处理（test 3、4 通过）
- [x] Checkpoint 22: 测试脚本验证 LLMState 搜索状态字段存在（test 2 通过）
- [x] Checkpoint 23: 单元测试全部通过（6/6）

## 日志与可观测性验证
- [x] Checkpoint 24: 工具调用有详细日志（工具名称、参数、结果数量）
- [x] Checkpoint 25: 每轮工具调用有轮数记录（`[LLM] 工具调用第 {n} 轮`）
- [x] Checkpoint 26: 工具失败有错误日志记录（`[LLM] 工具异常`、`[LLM] 工具超时`）
