# Live2D 结构化输出 (Structured Output) - Product Requirement Document

## Overview
- **Summary**: 重新设计 Live2D 控制系统，从脆弱的字符串拆分解析（`【语气】内容|目光|嘴巴`）迁移到结构化输出（JSON Schema + Pydantic 模型），实现可靠、可扩展、可验证的 AI 响应解析。
- **Purpose**: 当前的字符串拆分方式容易出错、不可扩展、缺乏结构验证，当 LLM 输出格式稍有偏差就会解析失败。结构化输出能确保 AI 返回的数据符合预期格式。
- **Target Users**: 虚拟主播系统开发者、维护者

## Goals
- **G1**: 将 Live2D 响应格式从字符串拆分迁移到 JSON 结构化输出
- **G2**: 定义 Live2D 响应的 Pydantic 数据模型（Live2DResponse）
- **G3**: 在 LLM 调用中使用 Response Format 约束输出结构
- **G4**: 实现可靠的 JSON 解析 + 校验 + 降级流程
- **G5**: 保持向后兼容，支持旧格式的降级解析

## Non-Goals (Out of Scope)
- 不改变 Live2D 控制器的底层实现（move_to_direction, set_mouth_state 等）
- 不修改前端 WebUI 的数据展示逻辑
- 不改变 TTS 的音频合成流程
- 不添加新的 Live2D 动作类型

## Background & Context

### 当前实现（问题）
```python
# 响应格式：【语气】内容|目光方向|嘴巴状态
response = "【开心】今天天气真好呢~|up|open"

# 解析逻辑：字符串拆分
end_bracket = response.find("】")
extracted_tone = response[1:end_bracket]
rest = response[end_bracket + 1:].strip()
parts = rest.split("|")
result["content"] = parts[0]
result["visual_focus"] = parts[1] if len(parts) > 1 else "center"
```

### 问题清单
| 问题 | 说明 |
|------|------|
| **脆弱解析** | 依赖 `【】` 和 `|` 分隔符，LLM 输出稍有偏差就会失败 |
| **无结构验证** | 只是简单拆分，不验证字段是否在合法范围内 |
| **不可扩展** | 添加新字段（如表情、手势）需要修改格式字符串和解析逻辑 |
| **LLM 不稳定** | 要求 LLM 严格遵循格式，但 LLM 输出本身有不确定性 |
| **错误静默** | 解析失败时静默使用默认值，用户无法感知 |

### 目标架构
```
LLM 调用 → 返回 JSON（结构化） → Pydantic 校验 → Live2D 控制器
                                    ↘ 降级（旧格式解析）
```

## Functional Requirements

### FR-1: Live2D 响应数据模型
定义 Pydantic 模型 `Live2DResponse`：
```python
class Live2DResponse(BaseModel):
    tone: Literal[ALLOWED_TONES]       # 语气（枚举约束）
    content: str                        # 回复内容
    visual_focus: Literal[VALID_DIRS]   # 目光方向
    mouth_state: Literal["open", "close"]  # 嘴巴状态
```

### FR-2: 结构化输出约束
- 在 LLM 调用时，通过 Response Format / Schema 约束输出格式为 JSON
- 定义 JSON Schema 描述期望的输出结构
- 要求 LLM 严格遵循 Schema 输出

### FR-3: 可靠解析流程
1. 尝试解析 JSON 响应
2. 使用 Pydantic 模型校验字段合法性
3. 校验失败时使用旧格式字符串解析作为降级
4. 解析完全失败时使用默认值并记录日志

### FR-4: 枚举值约束
- `tone`: 严格限定在 ALLOWED_TONES 集合内
- `visual_focus`: 严格限定在 VALID_DIRECTIONS 集合内
- `mouth_state`: 严格限定在 ["open", "close"] 内
- 非法值自动回退到默认值

### FR-5: 向后兼容
- 保留旧格式解析逻辑作为降级方案
- 当 JSON 解析失败时，回退到字符串拆分
- 确保升级后旧配置仍可使用

## Non-Functional Requirements

### NFR-1: 可靠性
- JSON 解析成功率 > 99%
- 完全失败率 < 0.1%（使用默认值）
- 降级路径必须可用

### NFR-2: 可维护性
- 数据模型集中定义（单一数据源）
- 添加新字段只需修改模型和 Schema
- 解析逻辑单元测试覆盖

### NFR-3: 性能
- JSON 解析耗时 < 5ms
- 不显著增加 LLM 调用延迟

## Constraints
- **Technical**: 
  - 豆包模型支持 Response Format / JSON Schema（需确认）
  - 已安装 pydantic 库
- **Dependencies**:
  - `live2d/` 目录下的 Live2D 控制器
  - `chat_model.py` 中的 LLM 调用逻辑
  - `main.py` 中的 parse_response_format 函数

## Assumptions
- 豆包/火山引擎模型支持 JSON Schema 模式的结构化输出
- 现有 ALLOWED_TONES、VALID_DIRECTIONS 常量保持不变
- Live2D 控制器接口稳定，无需修改

## Acceptance Criteria

### AC-1: 数据模型定义
- **Given**: Live2D 响应数据模型
- **When**: 开发者查看模型定义
- **Then**: 模型使用 Pydantic BaseModel，字段有类型约束和枚举限制
- **Verification**: `programmatic`

### AC-2: JSON 解析流程
- **Given**: LLM 返回的 JSON 格式响应
- **When**: 调用解析函数
- **Then**: 成功解析为 Live2DResponse 对象，字段值通过枚举验证
- **Verification**: `programmatic`

### AC-3: 降级处理
- **Given**: LLM 返回非 JSON 格式或非法 JSON
- **When**: 调用解析函数
- **Then**: 自动降级到旧格式字符串解析，不抛出异常
- **Verification**: `programmatic`

### AC-4: 枚举约束
- **Given**: JSON 中包含非法的 tone 值
- **When**: Pydantic 校验
- **Then**: 校验失败，降级到字符串解析或使用默认值
- **Verification**: `programmatic`

### AC-5: 向后兼容
- **Given**: 旧格式字符串 `【开心】内容|up|open`
- **When**: 调用解析函数
- **Then**: 正确解析为 Live2DResponse(tone="开心", content="内容", ...)
- **Verification**: `programmatic`

### AC-6: 单元测试覆盖
- **Given**: 解析函数
- **When**: 运行单元测试
- **Then**: 覆盖正常路径、JSON 解析、降级路径、边界情况，通过率 100%
- **Verification**: `programmatic`

## Open Questions
- [ ] 豆包模型是否原生支持 JSON Schema 约束？还是需要通过提示词引导？
- [ ] 是否需要添加流式解析支持？
- [ ] 是否需要记录解析成功/失败率用于监控？
