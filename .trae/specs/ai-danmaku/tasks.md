# AI 弹幕增强 - The Implementation Plan (Decomposed and Prioritized Task List)

## [x] Task 1: 添加 config.ini 配置项和配置加载
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 在 `config.ini` 中新增 `[danmu_tts]` 和 `[vision_danmu]` 两个配置段
  - `[danmu_tts]` 段包含：`enabled`（bool，默认 false）、`read_interval`（秒，默认 2）、`max_text_length`（默认 100）、`clean_emoji`（默认 true）
  - `[vision_danmu]` 段包含：`enabled`（bool，默认 false）、`capture_interval`（秒，默认 5）、`target_window`（默认空=全屏）、`persona`（视觉弹幕人设提示词）、`max_comment_length`（默认 30）、`cooldown`（冷却秒数，默认 10）
  - 在 `main.py` 启动流程中添加配置加载逻辑
  - 支持命令行参数 `--danmu-tts` 和 `--vision-danmu`
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-8
- **Test Requirements**:
  - `programmatic` TR-1.1: config.ini 新增 `[danmu_tts]` 和 `[vision_danmu]` 段，所有字段有默认值 ✅
  - `programmatic` TR-1.2: 启动时正确加载配置，日志打印加载结果 ✅
  - `programmatic` TR-1.3: `--danmu-tts` 和 `--vision-danmu` 命令行参数可覆盖 config.ini 设置 ✅
  - `programmatic` TR-1.4: 配置优先级验证（命令行 > config.ini > 默认值）✅
- **Notes**: 复用现有 `configparser` 加载机制

## [x] Task 2: 实现 AI 读弹幕 TTS 模块
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 创建 `danmu_tts/` 包，包含以下模块：
    - `__init__.py`: 导出 DanmuTtsPlayer 类
    - `player.py`: DanmuTtsPlayer 类，实现互斥播放逻辑
    - `service.py`: DanmuTtsService 类，编排弹幕→TTS→播放流程
  - DanmuTtsPlayer 核心功能：
    - `is_busy()`: 检查当前是否正在播放
    - `play_text(text: str) -> bool`: 发送文本到 GPT-SoVITS 并播放，返回是否成功开始播放
    - `stop()`: 停止当前播放
  - DanmuTtsService 核心功能：
    - `on_danmaku(message: str)`: 弹幕到达时调用，异步触发 TTS 朗读
    - `_synthesize_and_play(text)`: 调用 GPT-SoVITS API 合成并播放
    - 复用 `audio/audio_main.py` 中的 `clean_text_for_tts` 和 TTS API 调用逻辑
  - 在 `main.py` 的消息接收处理中集成 DanmuTtsService
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-5, AC-7
- **Test Requirements**:
  - `programmatic` TR-2.1: DanmuTtsPlayer.busy 状态正确切换（播放中/空闲）✅
  - `programmatic` TR-2.2: busy 期间新请求被丢弃，返回 False ✅
  - `programmatic` TR-2.3: DanmuTtsService.on_danmaku 正确触发 TTS 合成 ✅
  - `programmatic` TR-2.4: TTS 服务不可用时 on_danmaku 不抛异常 ✅
  - `programmatic` TR-2.5: clean_text_for_tts 正确清理弹幕中的 emoji ✅
- **Notes**: 音频播放可复用现有 `sounddevice` 或 GPT-SoVITS 返回的音频直接播放

## [x] Task 3: 实现视觉弹幕截图与分析模块
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 创建 `vision_danmu/` 包，包含以下模块：
    - `__init__.py`: 导出 VisionDanmuService 类
    - `service.py`: VisionDanmuService 类，编排截图→分析→注入消息队列
  - VisionDanmuService 核心功能：
    - `start()`: 启动定时截图循环
    - `stop()`: 停止定时截图循环
    - `_on_capture_tick()`: 定时触发，截图→分析→生成评论
    - `_generate_comment(image_base64)`: 调用视觉模型生成弹幕评论
    - 生成的评论以 `source=vision` 标记注入 `_message_queue`
  - 复用 `tool/vision_tool.py` 中的截图和视觉分析功能
  - 实现冷却期机制，避免连续相似截图生成重复评论
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-7
- **Test Requirements**:
  - `programmatic` TR-3.1: VisionDanmuService 按配置间隔定时触发截图 ✅
  - `programmatic` TR-3.2: 截图成功后正确调用视觉模型 ✅
  - `programmatic` TR-3.3: 视觉分析结果以正确格式注入消息队列 ✅
  - `programmatic` TR-3.4: 视觉分析超时后跳过当前帧 ✅
  - `programmatic` TR-3.5: 冷却期内不重复生成评论 ✅
  - `programmatic` TR-3.6: 关闭功能后定时器正确停止 ✅
- **Notes**: 视觉弹幕评论 prompt 需引导模型生成短弹幕风格文本（20-30字）

## [x] Task 4: 集成到主流程和 WebUI
- **Priority**: high
- **Depends On**: Task 2, Task 3
- **Description**:
  - 在 `main.py` 中集成 DanmuTtsService 和 VisionDanmuService
  - 添加 WebUI API 端点（配置管理 + 状态查询）
  - 添加条件导入、全局变量、初始化、关闭清理逻辑
- **Acceptance Criteria Addressed**: AC-1, AC-3, AC-4, AC-6
- **Test Requirements**:
  - `programmatic` TR-4.1: 弹幕到达时 DanmuTtsService.on_danmaku 被正确调用 ✅
  - `programmatic` TR-4.2: VisionDanmuService 启动/停止不影响 LangGraph 主流程 ✅
  - `programmatic` TR-4.3: WebUI API 端点代码已添加 ✅
  - `programmatic` TR-4.4: 模块正确导入 ✅
  - `human-judgement` TR-4.5: WebUI 前端控制开关和状态显示（需实际运行验证）
- **Notes**: 视觉弹幕消息需要在前端区分显示

## [x] Task 5: 添加日志和可观测性
- **Priority**: medium
- **Depends On**: Task 2, Task 3
- **Description**:
  - AI 读弹幕 TTS 日志：INFO/DEBUG/ERROR 级别
  - 视觉弹幕日志：INFO/DEBUG/ERROR 级别
  - WebUI 状态端点返回统计信息
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `programmatic` TR-5.1: 弹幕朗读关键操作有对应日志输出 ✅
  - `programmatic` TR-5.2: 视觉弹幕关键操作有对应日志输出 ✅
  - `programmatic` TR-5.3: 错误情况有 ERROR 级别日志 ✅
  - `programmatic` TR-5.4: WebUI 状态端点返回正确统计数据 ✅

## [x] Task 6: 单元测试
- **Priority**: medium
- **Depends On**: Task 2, Task 3, Task 5
- **Description**:
  - DanmuTtsPlayer 测试：busy 状态、播放互斥、stop 功能 ✅
  - VisionDanmuService 测试：冷却期、状态统计、消息格式、截断、禁用检查、失败处理 ✅
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-5, AC-7
- **Test Requirements**:
  - `programmatic` TR-6.1: 所有测试用例通过 ✅
  - `programmatic` TR-6.2: 正常流程测试覆盖 ✅
  - `programmatic` TR-6.3: 异常场景测试覆盖 ✅
  - `programmatic` TR-6.4: 边界条件测试覆盖 ✅

## [x] Task 7: 集成测试与验证
- **Priority**: medium
- **Depends On**: Task 4, Task 6
- **Description**:
  - 语法检查：所有文件 py_compile 通过 ✅
  - 单元测试：DanmuTtsPlayer 6/6 通过，VisionDanmuService 7/7 通过 ✅
  - 配置验证：config.ini 包含新配置段且值正确 ✅
  - 模块导入：danmu_tts 和 vision_danmu 可正确导入 ✅
  - 端到端：需实际启动服务验证（硬件依赖）
- **Acceptance Criteria Addressed**: AC-1, AC-3, AC-4, AC-6, AC-7, AC-8
- **Test Requirements**:
  - `programmatic` TR-7.1: 语法检查通过 ✅
  - `programmatic` TR-7.2: 单元测试通过 ✅
  - `programmatic` TR-7.3: 配置验证通过 ✅
  - `human-judgement` TR-7.4: 需实际运行服务验证（用户自行测试）
