# AI 弹幕增强 - Product Requirement Document

## Overview
- **Summary**: 在现有 LLM_vtuber 项目基础上，新增 AI 读弹幕 TTS 和视觉弹幕两大功能模块。AI 读弹幕 TTS 将直播间弹幕消息通过 TTS 引擎朗读出来，支持与现有 GPT-SoVITS TTS 无缝集成；视觉弹幕定时截取屏幕画面，交给视觉模型分析当前内容并生成弹幕评论，实现"AI 观众"功能。
- **Purpose**: 解决虚拟主播只有文字回复、缺乏语音播报和视觉感知能力的问题。让虚拟主播能够"听到"观众弹幕并朗读，同时能"看到"屏幕内容并做出反应。
- **Target Users**: 虚拟主播运营者、直播内容创作者、希望为直播间添加 AI 互动能力的个人用户。

## Goals
- 实现 AI 读弹幕 TTS 功能，将直播间收到的弹幕消息以语音形式朗读
- 实现视觉弹幕功能，定时截图并通过视觉模型生成弹幕评论
- 保持与现有 TTS（GPT-SoVITS）的兼容性，可独立开关
- 保持与现有 LangGraph 流程的无缝集成
- 支持通过 config.ini 配置所有新功能参数
- 支持通过 WebUI 控制新功能的开关和参数

## Non-Goals (Out of Scope)
- 不替换现有 GPT-SoVITS TTS 服务
- 不实现弹幕浮层渲染（透明置顶弹幕层）
- 不实现 PyQt6 GUI 界面（项目基于 FastAPI WebUI）
- 不实现麦克风输入功能
- 不实现多显示器截图支持（v2 考虑）
- 不实现 Live2D 模型导入/管理功能

## Background & Context
- 现有项目已具备：Bilibili 弹幕监听、LLM 对话生成、Live2D 驱动、GPT-SoVITS TTS、视觉分析工具（单张截图分析）、Milvus 向量记忆存储
- 参考项目 PEPETII/danmuai 实现了完整的 AI 弹幕生态，其核心架构包括：
  - `DanmuReadService`：定时从屏幕可见弹幕抽样 → TTS 合成 → 本地播放
  - `DanmuTtsPlayback`：互斥播放管理，支持 busy 状态检测
  - 截图定时器 → 视觉模型 → 弹幕生成 → 弹幕引擎渲染
- 现有项目的 TTS 节点（`tts_node`）仅处理 AI 回复的语音合成，不处理弹幕朗读
- 现有项目的 `vision_tool.py` 已具备截图和视觉分析能力，但仅作为工具供 LLM 主动调用

## Functional Requirements

### FR-1: AI 读弹幕 TTS
- **FR-1.1**: 系统可配置是否朗读弹幕（`[danmu_tts] enabled`）
- **FR-1.2**: 当弹幕到达时，系统将弹幕内容发送到 TTS 引擎进行语音合成
- **FR-1.3**: TTS 合成结果通过音频播放设备播放，支持互斥播放（避免重叠语音）
- **FR-1.4**: 支持 TTS 朗读间隔配置（避免连续弹幕导致语音过载）
- **FR-1.5**: 支持 TTS 文本预处理（清理 emoji、特殊字符，与现有 `clean_text_for_tts` 逻辑一致）
- **FR-1.6**: 弹幕朗读可独立于 AI 回复 TTS 开关，两个 TTS 通道互不干扰

### FR-2: 视觉弹幕
- **FR-2.1**: 系统可配置是否启用视觉弹幕（`[vision_danmu] enabled`）
- **FR-2.2**: 按配置的时间间隔（默认 5 秒）自动截取屏幕画面
- **FR-2.3**: 将截图发送到视觉模型进行分析，生成弹幕评论内容
- **FR-2.4**: 视觉弹幕评论作为普通弹幕消息注入消息队列，走现有 LLM 处理流程
- **FR-2.5**: 支持配置截图目标（全屏或指定窗口）
- **FR-2.6**: 支持配置视觉弹幕的系统提示词（persona/style）
- **FR-2.7**: 视觉弹幕与真实弹幕在消息队列中可区分（通过 `source` 字段标记 `vision`）

### FR-3: 配置与控制
- **FR-3.1**: 所有新功能参数可通过 `config.ini` 配置
- **FR-3.2**: 支持通过 WebUI API 动态开关功能
- **FR-3.3**: 支持通过 WebUI 查看功能运行状态（队列长度、最近处理时间等）
- **FR-3.4**: 支持通过命令行参数 `--danmu-tts` 和 `--vision-danmu` 临时启用

### FR-4: 消息处理流程
- **FR-4.1**: 视觉弹幕消息注入 `_message_queue`，与真实弹幕共享处理管线
- **FR-4.2**: AI 读弹幕 TTS 在弹幕到达事件中触发，异步执行不阻塞主流程
- **FR-4.3**: TTS 播放失败不影响消息处理流程
- **FR-4.4**: 视觉分析失败时记录日志并跳过，不影响后续截图

## Non-Functional Requirements

### NFR-1: 性能
- 视觉弹幕截图间隔最小支持 3 秒，建议 5-15 秒
- AI 读弹幕 TTS 延迟（从弹幕到音频播放）< 3 秒（取决于 TTS 服务响应时间）
- 视觉分析单帧处理时间 < 30 秒（超时则跳过）

### NFR-2: 可靠性
- TTS 播放失败自动重试（最多 1 次）
- 视觉分析失败后下次定时器继续触发
- 弹幕朗读 busy 状态下丢弃新请求（不排队，避免语音堆积）

### NFR-3: 兼容性
- 与现有 GPT-SoVITS TTS 服务完全兼容，复用音频播放逻辑
- 与现有 LangGraph 流程无缝集成，通过可选节点扩展
- 配置优先级：命令行 > config.ini > 代码默认值

### NFR-4: 可观测性
- 关键操作记录 INFO 级别日志
- 错误情况记录 ERROR 级别日志
- WebUI 状态页面显示功能开关和运行统计

## Constraints
- **Technical**: Python 3.10+、FastAPI、LangGraph、GPT-SoVITS TTS 服务、豆包视觉模型 API
- **Business**: 需兼容现有代码架构，不破坏已有功能
- **Dependencies**: 
  - GPT-SoVITS TTS 服务（运行在端口 9880）
  - 豆包视觉模型 API（已配置 `[vision]` 段）
  - sounddevice 库用于音频播放（可选，若复用现有播放逻辑则不需要）

## Assumptions
- GPT-SoVITS TTS 服务已正常运行
- 豆包视觉模型 API Key 已在环境变量或 config.ini 中配置
- 用户理解视觉弹幕会消耗额外的 API Token
- AI 读弹幕 TTS 复用现有 TTS 服务端口和协议

## Acceptance Criteria

### AC-1: AI 读弹幕 TTS 正常工作
- **Given**: 系统已启动，`[danmu_tts] enabled = true`，GPT-SoVITS TTS 服务正常运行
- **When**: Bilibili 直播间收到一条弹幕消息"你好"
- **Then**: 系统通过 TTS 引擎合成语音并播放"你好"，日志记录"弹幕朗读成功"
- **Verification**: `programmatic`
- **Notes**: 需验证音频实际播放

### AC-2: AI 读弹幕 TTS 可独立关闭
- **Given**: 系统已启动，`[danmu_tts] enabled = false`
- **When**: Bilibili 直播间收到弹幕消息
- **Then**: 弹幕正常走 LLM 处理流程，但不触发 TTS 朗读
- **Verification**: `programmatic`

### AC-3: 视觉弹幕定时截图正常
- **Given**: 系统已启动，`[vision_danmu] enabled = true`，截图间隔设为 5 秒
- **When**: 等待 5 秒
- **Then**: 系统截取屏幕截图，发送到视觉模型分析，生成一条弹幕评论注入消息队列
- **Verification**: `programmatic`
- **Notes**: 检查日志和消息队列

### AC-4: 视觉弹幕消息走 LLM 流程
- **Given**: 视觉弹幕已生成评论消息
- **When**: 该消息进入消息队列
- **Then**: 消息被 LangGraph 流程处理，AI 生成回复，回复中包含对视觉内容的评论
- **Verification**: `programmatic`

### AC-5: TTS 播放互斥
- **Given**: TTS 正在播放一条弹幕
- **When**: 新弹幕到达触发 TTS 朗读请求
- **Then**: 新请求被丢弃（不排队），日志记录"busy，跳过"
- **Verification**: `programmatic`

### AC-6: WebUI 可控制新功能
- **Given**: 系统已启动
- **When**: 用户通过 WebUI 调用 `/api/config/danmu_tts` 或 `/api/config/vision_danmu`
- **Then**: 功能开关立即生效，状态变更在 WebUI 页面反映
- **Verification**: `programmatic`

### AC-7: 异常不影响主流程
- **Given**: TTS 服务不可用 或 视觉模型 API 超时
- **When**: 弹幕到达或截图定时触发
- **Then**: 错误被记录日志，主流程（弹幕接收 → LLM 处理 → 回复生成）不受影响
- **Verification**: `programmatic`

### AC-8: 配置持久化
- **Given**: 用户修改了 `[danmu_tts]` 或 `[vision_danmu]` 配置
- **When**: 用户重启系统
- **Then**: 新配置被正确加载
- **Verification**: `programmatic`

## Open Questions
- [ ] AI 读弹幕 TTS 是否需要支持不同的 TTS provider（除 GPT-SoVITS 外），还是仅复用现有服务？
- [ ] 视觉弹幕是否需要限制每条评论的字数（如 20 字以内），以匹配弹幕风格？
- [ ] 视觉弹幕的 prompt 是否需要根据虚拟主播人设动态调整？
- [ ] 是否需要实现视觉弹幕的"冷却期"（两次视觉分析之间的最小间隔），避免重复内容？
