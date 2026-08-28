# Live2D 结构化输出 - The Implementation Plan

## [x] Task 1: 定义 Live2D 响应数据模型
- **Priority**: high
- **Depends On**: None
- **Description**: 
  - 创建 `Live2DResponse` Pydantic 模型类
  - 定义字段：tone, content, visual_focus, mouth_state
  - 使用 Literal 约束枚举值范围
  - 添加字段验证器（可选）
  - 在 `chat_model.py` 或新文件中定义
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 模型类继承自 BaseModel
  - `programmatic` TR-1.2: tone 字段使用 Literal 约束 ALLOWED_TONES
  - `programmatic` TR-1.3: visual_focus 字段使用 Literal 约束 VALID_DIRECTIONS
  - `programmatic` TR-1.4: mouth_state 字段使用 Literal 约束 ["open", "close"]
  - `programmatic` TR-1.5: content 字段为 str 类型
- **Notes**: 枚举值从 main.py 导入或定义在模型文件中

## [x] Task 2: 实现 JSON 解析与校验逻辑
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 实现 `parse_structured_response()` 函数
  - 首先尝试 JSON 解析 + Pydantic 校验
  - 校验失败时降级到旧格式字符串解析
  - 添加详细日志记录解析过程
  - 返回统一的字典格式
- **Acceptance Criteria Addressed**: AC-2, AC-3, AC-4
- **Test Requirements**:
  - `programmatic` TR-2.1: JSON 格式成功解析为 Live2DResponse
  - `programmatic` TR-2.2: 非法 JSON 降级到字符串解析
  - `programmatic` TR-2.3: 非法枚举值降级到默认值
  - `programmatic` TR-2.4: 完全失败时返回默认值并记录错误日志
- **Notes**: 保留旧格式解析函数作为降级路径

## [x] Task 3: 定义 JSON Schema 并更新提示词
- **Priority**: high
- **Depends On**: Task 1
- **Description**: 
  - 定义 `LIVE2D_RESPONSE_SCHEMA` JSON Schema
  - 在系统提示词中添加结构化输出说明
  - 明确要求 AI 返回 JSON 格式
  - 提供示例 JSON 输出
  - 保持降级提示（如果无法返回 JSON，使用旧格式）
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-3.1: JSON Schema 包含所有字段定义
  - `programmatic` TR-3.2: Schema 中包含枚举值约束
  - `programmatic` TR-3.3: 系统提示词中包含 JSON 输出示例
  - `human-judgement` TR-3.4: 提示词清晰指导 AI 输出正确的 JSON 格式
- **Notes**: Schema 可用于 function calling 的 response_format

## [x] Task 4: 集成到主流程
- **Priority**: high
- **Depends On**: Task 2, Task 3
- **Description**: 
  - 修改 `_llm_process_node` 使用新的解析函数
  - 替换 `parse_response_format` 调用为 `parse_structured_response`
  - 传递 JSON Schema 给 LLM 调用（如支持）
  - 保持现有状态更新逻辑不变
  - 添加解析结果的日志记录
- **Acceptance Criteria Addressed**: AC-2, AC-3
- **Test Requirements**:
  - `programmatic` TR-4.1: 主流程使用新的解析函数
  - `programmatic` TR-4.2: 解析结果正确传递到后续节点（Live2D、WebSocket）
  - `programmatic` TR-4.3: 降级路径在主流程中可用
- **Notes**: 需要仔细测试整个链路

## [x] Task 5: 编写单元测试
- **Priority**: high
- **Depends On**: Task 2, Task 4
- **Description**: 
  - 编写 `test_structured_output.py` 测试脚本
  - 测试场景：
    1. 标准 JSON 解析成功
    2. 非法 JSON 降级到字符串解析
    3. 非法枚举值降级
    4. 空响应/None 处理
    5. 旧格式字符串解析
    6. 边界情况（特殊字符、超长内容等）
  - 验证所有解析路径的正确性
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-5, AC-6
- **Test Requirements**:
  - `programmatic` TR-5.1: 标准 JSON 解析测试通过
  - `programmatic` TR-5.2: 降级路径测试通过
  - `programmatic` TR-5.3: 枚举约束测试通过
  - `programmatic` TR-5.4: 旧格式兼容测试通过
  - `programmatic` TR-5.5: 所有测试通过率 100%
- **Notes**: 测试覆盖率应尽可能高

## [x] Task 6: 验证与清理
- **Priority**: medium
- **Depends On**: Task 5
- **Description**: 
  - 运行完整单元测试
  - 代码审查确保无遗留问题
  - 更新相关文档（如有）
  - 清理调试代码和临时文件
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `programmatic` TR-6.1: 所有单元测试通过
  - `programmatic` TR-6.2: 语法检查通过（python -m py_compile）
  - `human-judgement` TR-6.3: 代码质量审查通过
- **Notes**: 最终验证步骤
