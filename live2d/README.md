# Live2D VTube Studio API Integration

本文件夹包含与 VTube Studio API 交互的相关文件，用于实现 LLM_vtuber 项目中与 Live2D 虚拟主播的交互功能。
文件结构：
live2d_base_centrol.py：live2d控制代码
通过切换状态来控制live2d模型的行为
RequestHandler(BaseHTTPRequestHandler)获取请求并解析json，设置状态

vtuber_studio_info.py：基于vtuber_studio原文档的所有python操作实现
vtuberapi.md:vtuber_studio原文档
## 文件夹内容

### 1. `vtuber_studio_info.py`

这是一个 Python 模块，实现了与 VTube Studio API 交互的核心功能。它包含 `VTubeStudioAPI` 类，提供了完整的 API 客户端实现，包括：

- **连接管理**：连接到 VTube Studio 服务器并处理断开连接
- **认证系统**：请求和管理 API 认证令牌
- **服务器发现**：通过 UDP 广播自动发现 VTube Studio 服务器
- **API 请求**：发送各种 API 请求并处理响应
- **模型控制**：获取、加载和移动模型
- **热键控制**：获取和触发热键
- **表情控制**：获取和激活/停用表情
- **统计信息**：获取 VTS 统计数据和文件夹信息

#### 主要类和方法

```python
class VTubeStudioAPI:
    # 连接和认证方法
    async def connect()
    async def disconnect()
    async def request_auth_token(plugin_name, plugin_developer)
    async def authenticate(plugin_name=None, plugin_developer=None)
    def set_auth_token(token, plugin_name, plugin_developer)
    
    # 服务器发现
    @staticmethod
    async def discover_servers(timeout=3.0)
    
    # 模型相关
    async def get_available_models()
    async def get_current_model()
    async def load_model(model_id)
    async def move_model(time_in_seconds, values_are_relative_to_model, ...)
    
    # 热键相关
    async def get_hotkeys()
    async def trigger_hotkey(hotkey_id, item_instance_id=None)
    
    # 表情相关
    async def get_expression_state(details=True, expression_file=None)
    async def activate_expression(expression_file, active, fade_time=0.25)
    
    # 其他功能
    async def get_statistics()
    async def get_vts_folders()
    async def get_face_tracking_data()
    # ... 更多方法
```

### 2. `vtuberapi.md`

这是 VTube Studio API 的官方文档，详细介绍了 API 的使用方法、请求格式、响应格式和各种功能。文档内容包括：

- API 概述和基本信息
- 认证流程
- 事件订阅
- 模型管理
- 热键控制
- 表情控制
- 参数控制
- 物品管理
- 等等...

这是开发和使用 VTube Studio API 的重要参考资料。

## 代码结构

### `VTubeStudioAPI` 类的核心组件

1. **连接层**
   - 使用 WebSocket 与 VTube Studio 服务器通信
   - 处理连接建立、断开和重连

2. **认证层**
   - 请求和管理认证令牌
   - 处理会话认证
   - 支持令牌持久化（通过 `set_auth_token` 方法）

3. **请求层**
   - 构建和发送 API 请求
   - 处理响应和错误
   - 支持异步操作

4. **功能层**
   - 模型管理功能
   - 热键和表情控制
   - 参数和统计信息获取
   - 事件处理（可扩展）

## 使用示例

以下是使用 `VTubeStudioAPI` 类的基本示例：

```python
import asyncio
from vtuber_studio_info import VTubeStudioAPI

async def main():
    # 创建 API 客户端
    api = VTubeStudioAPI()
    
    try:
        # 连接到服务器
        await api.connect()
        
        # 请求认证令牌
        await api.request_auth_token("MyLLMPlugin", "LLM_vtuber")
        
        # 等待用户在 VTS 界面授权
        input("请在 VTube Studio 中授权插件，然后按 Enter 继续...")
        
        # 进行会话认证
        await api.authenticate()
        
        # 获取当前加载的模型
        model_info = await api.get_current_model()
        print(f"当前模型: {model_info['data']['modelName']}")
        
        # 移动模型
        await api.move_model(0.5, False, position_x=0.1, position_y=-0.1)
        
        # 获取热键列表
        hotkeys = await api.get_hotkeys()
        print(f"可用热键数: {len(hotkeys['data']['availableHotkeys'])}")
        
    finally:
        # 断开连接
        await api.disconnect()

# 运行主函数
asyncio.run(main())
```

## 依赖项

- `asyncio`: Python 异步编程库
- `websockets`: WebSocket 客户端库
- `json`: JSON 数据处理（Python 标准库）
- `uuid`: 生成唯一标识符（Python 标准库）
- `socket`: 网络通信（Python 标准库）
- `struct`: 二进制数据处理（Python 标准库）

## 安装依赖

```bash
pip install websockets
```

## 参考文档

- [VTube Studio API 官方文档](https://github.com/DenchiSoft/VTubeStudio/blob/master/VTubeStudioPublicAPI.md)
- [VTube Studio 插件开发指南](https://github.com/DenchiSoft/VTubeStudio/wiki/Plugins)

## 注意事项

1. 使用前需要在 VTube Studio 中启用 API 访问（设置 -> 插件 API -> 允许插件 API 访问）
2. 首次使用需要在 VTube Studio 界面授权插件访问
3. 支持的 VTube Studio 版本：1.9.0 及以上
4. 所有 API 调用都是异步的，需要在异步函数中使用

## 扩展功能

`VTubeStudioAPI` 类设计为可扩展的，用户可以通过继承该类并重写或添加方法来扩展功能，例如：

```python
class MyExtendedAPI(VTubeStudioAPI):
    async def custom_event_handler(self):
        # 实现自定义事件处理逻辑
        pass
    
    async def my_custom_function(self):
        # 实现自定义功能
        pass
```

## 许可证

本代码基于 VTube Studio API 开发，遵循 MIT 许可证。

---

如需了解更多关于 VTube Studio API 的详细信息，请参考 `vtuberapi.md` 文件或官方文档。