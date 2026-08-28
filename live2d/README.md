# Live2D VTube Studio API Integration

本文件夹包含与 VTube Studio API 交互的相关文件，用于实现 LLM_vtuber 项目中与 Live2D 虚拟主播的交互功能。

## 文件结构

| 文件 | 职责 |
|------|------|
| `live2d_main.py` | **核心控制**：idle_movement 常态循环、set_expression 表情注入、update_non_moving_state 目光移动、dump_vts_current_params 诊断快照 |
| `vtuber_studio_info.py` | **VTS API 封装**：VTubeStudioAPI 类，提供 WebSocket 连接、认证、所有 API 方法的异步实现 |
| `live2d_controller_manager.py` | **连接管理**：WebSocket 连接建立、重连、命令调度 |
| `live2d_base_centrol.py` | 旧版状态机控制（已弃用，保留参考） |

## 核心工作原理

### 高频参数注入

VTube Studio 摄像头追踪以 ~20Hz 覆盖参数。我们需要**更高频地注入**（每 0.3s tick 一次），且必须设置 `faceFound=True` 让 VTS 接受我们的值。

```
VTS 内部参数更新源：
┌────────────────────────────────┐
│ 摄像头追踪  (默认 ~20Hz)       │
│  ↓ 覆盖                        │
│ VTS 最终参数值                 │
│  ↑ 我们注入  (≥ 3.3Hz)        │
│  faceFound=True, mode="set"   │
└────────────────────────────────┘
```

### 参数名称的两个世界（重要）

| API | 返回参数名 | 用途 |
|------|------|------|
| `InputParameterListRequest` | `FaceAngleX`, `MouthOpen`, `EyeOpenLeft` ... | **我们用这个**：人类可读 |
| `Live2DParameterListRequest` | `Param158`, `Param159` ... | 不要用：模型内部占位名 |

## VTubeStudioAPI 主要方法

```python
class VTubeStudioAPI:
    # 连接与认证
    async def connect()
    async def disconnect()
    async def authenticate()
    
    # 参数控制
    async def inject_parameter_data(params, face_found=True, mode="set")
    async def get_tracking_parameters()    # InputParameterListRequest
    async def get_live2d_parameters()       # Live2DParameterListRequest
    
    # 表情与热键
    async def trigger_hotkey(hotkey_id)
    async def activate_expression(expression_file, active, fade_time)
    async def get_expression_state()
    async def get_hotkeys_in_model()
    
    # 模型
    async def get_current_model()
    async def load_model(model_id)
    async def move_model(time_in_seconds, ...)
    
    # 道具
    async def item_load_request(...)
    async def item_animation_control_request(...)
```

## 使用示例

```python
import asyncio
from live2d.live2d_main import Live2DMain

async def main():
    live2d = Live2DMain(host="localhost", port=8001)
    await live2d.connect()
    
    # 启动常态循环（呼吸 + 漂移）
    await live2d.start_idle()
    
    # 设置目光方向
    await live2d.set_direction("left")
    
    # 注入表情
    await live2d.set_expression("smile")
    
    await live2d.disconnect()

asyncio.run(main())
```

## 依赖项

- `asyncio`: Python 异步编程
- `websockets`: WebSocket 客户端
- `json`: JSON 数据处理（标准库）
- `uuid`: 生成唯一标识符（标准库）

## 参考文档

- [live2d_design.md](../docs/live2d_design.md) - Live2D 详细设计文档
- [live2d_optimization.md](../docs/live2d_optimization.md) - Live2D 优化方向与架构设计
- [VTube Studio API 官方文档](https://github.com/DenchiSoft/VTubeStudio/blob/master/VTubeStudioPublicAPI.md)

## 注意事项

1. VTS 需启用 API 访问（设置 → 插件 API → 允许）
2. 首次使用需在 VTS 界面授权插件
3. 模型加载有 3-5 秒延迟，启动后等待再发指令
4. 所有 WebSocket 操作必须持 `asyncio.Lock`（`operation_lock`）
5. 注入频率必须 ≥ 每 0.5s 一次，否则会被摄像头追踪抢回

## 许可证

基于 VTube Studio API 开发，遵循 MIT 许可证。
