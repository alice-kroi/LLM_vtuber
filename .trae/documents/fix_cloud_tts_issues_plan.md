# 云端 TTS 问题修复计划

## 问题概述

用户反馈两个问题：
1. **无法停止云端 TTS 功能** - 停止操作无效，TTS 仍然继续工作
2. **TTS 对 agent 自行思考也生效** - 只希望对最终回复生成 TTS，不希望对思考过程生成

## 问题分析

### 问题1：无法停止云端 TTS

**根因分析**：
- `disable_cloud_tts()` 函数（main.py:L1352-L1366）只设置 `args.cloud_tts = False` 并调用 `rebuild_graph()`
- 但没有同步设置 `cloud_tts_config.enabled = False`
- 正在进行的 TTS 任务无法被中断，会继续播放直到完成

**涉及文件**：
- `main.py` - `disable_cloud_tts()` 函数
- `audio/audio_main.py` - `cloud_tts_request_and_play()` 函数

### 问题2：TTS 对思考内容生效

**根因分析**：
- 在 `LLM/chat_model.py` 第 196-199 行：
  ```python
  if hasattr(msg, "content") and msg.content:
      ai_resp = msg.content
  elif hasattr(msg, "reasoning_content") and msg.reasoning_content:
      ai_resp = msg.reasoning_content
  ```
- 当模型开启思考模式时，`msg.content` 可能为空，`msg.reasoning_content` 包含思考过程
- 代码将 `reasoning_content` 作为响应返回，导致 TTS 对思考内容进行合成

**涉及文件**：
- `LLM/chat_model.py` - `_call_chat_api_with_retry()` 函数

## 修复方案

### 修复1：完善 `disable_cloud_tts()` 函数

**修改文件**：`main.py`

**修改内容**：
```python
async def disable_cloud_tts():
    """禁用云端 TTS 功能"""
    global args, cloud_tts_config

    if not (args and getattr(args, 'cloud_tts', False)):
        logger.info("云端 TTS 未启用")
        return True

    args.cloud_tts = False
    
    # 同步设置配置为 False
    if cloud_tts_config:
        cloud_tts_config.enabled = False
    
    logger.info("云端 TTS 已禁用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_cloud_tts=False)

    return True
```

### 修复2：TTS 节点检查配置状态

**修改文件**：`audio/audio_main.py`

**修改内容**：在 `cloud_tts_node()` 函数开头添加配置检查：
```python
async def cloud_tts_node(state) -> dict:
    # 检查云端 TTS 是否仍然启用
    from audio.cloud_tts import get_cloud_tts_config
    cfg = get_cloud_tts_config()
    if not cfg or not cfg.enabled:
        logger.info("云端 TTS 已禁用，跳过合成")
        return dict(state) if isinstance(state, dict) else state
    
    # ... 原有逻辑
```

### 修复3：修复思考内容泄漏问题

**修改文件**：`LLM/chat_model.py`

**修改内容**：只使用 `msg.content`，忽略 `reasoning_content`：
```python
# 修改前：
if hasattr(msg, "content") and msg.content:
    ai_resp = msg.content
elif hasattr(msg, "reasoning_content") and msg.reasoning_content:
    ai_resp = msg.reasoning_content

# 修改后：只使用正式回复内容
if hasattr(msg, "content") and msg.content:
    ai_resp = msg.content
else:
    ai_resp = ""  # 不使用 reasoning_content
```

## 修改文件清单

| 文件路径 | 修改类型 | 修改说明 |
|---------|---------|---------|
| `main.py` | 修改 | `disable_cloud_tts()` 添加 `cloud_tts_config.enabled = False` |
| `audio/audio_main.py` | 修改 | `cloud_tts_node()` 添加配置检查 |
| `LLM/chat_model.py` | 修改 | `_call_chat_api_with_retry()` 忽略 `reasoning_content` |

## 风险评估

- **低风险**：修改涉及局部逻辑，不影响其他功能
- **可回滚**：所有修改都可以通过 git revert 回滚
- **测试建议**：
  1. 启动程序，启用云端 TTS
  2. 发送弹幕测试 TTS 是否正常工作
  3. 通过 Web UI 停止云端 TTS，确认停止后不再有 TTS 播放
  4. 再次发送弹幕，确认只有回复内容被 TTS 合成

## 实施步骤

1. 修改 `LLM/chat_model.py` - 忽略 `reasoning_content`
2. 修改 `main.py` - 完善 `disable_cloud_tts()` 函数
3. 修改 `audio/audio_main.py` - 添加 TTS 节点配置检查
4. 测试验证
