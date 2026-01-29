import asyncio
import websockets
import json
import uuid
import socket
import struct

class VTubeStudioAPI:
    """VTube Studio API客户端类"""
    
    def __init__(self, host="localhost", port=8001):
        """初始化VTube Studio API客户端
        
        参数:
            host (str, optional): VTube Studio服务器主机名. 默认值 "localhost".
            port (int, optional): VTube Studio服务器端口. 默认值 8001.
        """
        self.host = host
        self.port = port
        self.websocket = None
        self.auth_token = None
        self.plugin_name = None
        self.plugin_developer = None
    
    async def connect(self):
        """连接到VTube Studio服务器
        
        返回:
            bool: 连接成功返回True，否则返回False
        """
        try:
            self.websocket = await websockets.connect(f"ws://{self.host}:{self.port}")
            print(f"已成功连接到VTube Studio服务器 ws://{self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"连接到VTube Studio服务器失败: {e}")
            return False
    
    async def disconnect(self):
        """断开与VTube Studio服务器的连接
        
        返回:
            bool: 断开成功返回True，否则返回False
        """
        try:
            if self.websocket:
                await self.websocket.close()
                print("已断开与VTube Studio服务器的连接")
                return True
        except Exception as e:
            print(f"断开连接时发生错误: {e}")
        return False
    
    async def send_request(self, api_name="VTubeStudioPublicAPI", api_version="1.0", 
                         request_id=None, message_type="APIStateRequest", data=None):
        """发送API请求到VTube Studio服务器
        
        参数:
            api_name (str, optional): API名称. 默认值 "VTubeStudioPublicAPI".
            api_version (str, optional): API版本. 默认值 "1.0".
            request_id (str, optional): 请求ID. 默认自动生成UUID.
            message_type (str, optional): 消息类型. 默认值 "APIStateRequest".
            data (dict, optional): 请求数据. 默认值 None.
        
        返回:
            dict: 服务器响应的JSON数据
        """
        if not self.websocket:
            print("未连接到服务器，请先调用connect()方法")
            return None
        
        # 生成请求ID（如果未提供）
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # 构建请求数据
        request_data = {
            "apiName": api_name,
            "apiVersion": api_version,
            "requestID": request_id,
            "messageType": message_type,
            "data": data or {}
        }
        
        try:
            # 发送请求
            await self.websocket.send(json.dumps(request_data))
            
            # 接收响应
            response = await self.websocket.recv()
            return json.loads(response)
        
        except websockets.ConnectionClosed:
            print("连接已关闭，无法发送请求")
            return None
        except json.JSONDecodeError:
            print("响应数据不是有效的JSON格式")
            return None
        except Exception as e:
            print(f"发送请求时发生错误: {e}")
            return None
    
    @staticmethod
    async def discover_servers(timeout=3.0):
        """使用UDP广播发现VTube Studio服务器
        
        参数:
            timeout (float, optional): 发现超时时间（秒）. 默认值 3.0.
        
        返回:
            list: 发现的VTube Studio服务器信息列表
        """
        discovered = []
        
        async def listen_udp():
            try:
                # 创建UDP套接字
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(0.1)  # 设置超时以便定期检查是否应该停止
                
                # 绑定到所有接口的47779端口
                sock.bind(("", 47779))
                
                print(f"正在监听UDP端口47779上的VTube Studio广播...")
                
                # 记录开始时间
                start_time = asyncio.get_event_loop().time()
                
                while asyncio.get_event_loop().time() - start_time < timeout:
                    try:
                        # 接收数据（最大65535字节）
                        data, addr = sock.recvfrom(65535)
                        
                        # 解析接收到的数据
                        message = json.loads(data.decode('utf-8'))
                        
                        # 检查是否是VTube Studio API状态广播
                        if (message.get("apiName") == "VTubeStudioPublicAPI" and
                            message.get("messageType") == "VTubeStudioAPIStateBroadcast"):
                            
                            data = message.get("data", {})
                            if data.get("active"):  # 只添加活动的API实例
                                server_info = {
                                    "active": data.get("active"),
                                    "port": data.get("port"),
                                    "instanceID": data.get("instanceID"),
                                    "windowTitle": data.get("windowTitle")
                                }
                                
                                # 避免重复添加相同的实例
                                if server_info not in discovered:
                                    discovered.append(server_info)
                                    
                    except socket.timeout:
                        # 超时是正常的，继续监听
                        continue
                    except json.JSONDecodeError:
                        print(f"从{addr}接收到无效的JSON数据")
                    except Exception as e:
                        print(f"监听UDP时发生错误: {e}")
                
                sock.close()
            except Exception as e:
                print(f"创建UDP套接字失败: {e}")
        
        # 运行监听任务
        await listen_udp()
        
        return discovered
    
    async def get_api_state(self, request_id=None):
        """获取API状态信息
        
        参数:
            request_id (str, optional): 请求ID. 默认自动生成UUID.
        
        返回:
            dict: 包含API状态信息的响应数据
        """
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="APIStateRequest",
            request_id=request_id,
            data={}
        )
    
    async def request_auth_token(self, plugin_name, plugin_developer):
        """请求认证令牌
        
        参数:
            plugin_name (str): 插件名称（3-32个字符）
            plugin_developer (str): 插件开发者名称（3-32个字符）
        
        返回:
            bool: 请求成功返回True，否则返回False
        """
        # 参数验证
        if len(plugin_name) < 3 or len(plugin_name) > 32:
            print("插件名称长度必须在3-32个字符之间")
            return False
        
        if len(plugin_developer) < 3 or len(plugin_developer) > 32:
            print("插件开发者名称长度必须在3-32个字符之间")
            return False
        
        try:
            response = await self.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="AuthenticationTokenRequest",
                data={
                    "pluginName": plugin_name,
                    "pluginDeveloper": plugin_developer
                }
            )
            
            if response:
                if response.get("messageType") == "AuthenticationTokenResponse":
                    token_data = response.get("data", {})
                    self.auth_token = token_data.get("authenticationToken")
                    self.plugin_name = plugin_name
                    self.plugin_developer = plugin_developer
                    
                    print(f"认证令牌获取成功: {self.auth_token}")
                    print(f"请在VTube Studio界面中授权此插件")
                    return True
                elif response.get("messageType") == "APIError":
                    error_data = response.get("data", {})
                    error_id = error_data.get("errorID")
                    error_message = error_data.get("message", "未知错误")
                    
                    if error_id == 50:
                        print(f"用户拒绝了认证请求: {error_message}")
                    else:
                        print(f"获取认证令牌失败: [{error_id}] {error_message}")
                    
                    return False
        except Exception as e:
            print(f"请求认证令牌时发生错误: {e}")
        
        return False
    
    async def authenticate(self, plugin_name=None, plugin_developer=None):
        """使用获取的令牌进行会话认证
        
        参数:
            plugin_name (str, optional): 插件名称. 如果不提供则使用之前保存的名称.
            plugin_developer (str, optional): 插件开发者名称. 如果不提供则使用之前保存的名称.
        
        返回:
            bool: 认证成功返回True，否则返回False
        """
        if not self.auth_token:
            print("没有可用的认证令牌，请先调用request_auth_token()获取令牌")
            return False
        
        # 使用提供的值或之前保存的值
        plugin_name = plugin_name or self.plugin_name
        plugin_developer = plugin_developer or self.plugin_developer
        
        # 参数验证
        if not plugin_name or len(plugin_name) < 3 or len(plugin_name) > 32:
            print("插件名称长度必须在3-32个字符之间")
            return False
        
        if not plugin_developer or len(plugin_developer) < 3 or len(plugin_developer) > 32:
            print("插件开发者名称长度必须在3-32个字符之间")
            return False
        
        try:
            response = await self.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="AuthenticationRequest",
                data={
                    "pluginName": plugin_name,
                    "pluginDeveloper": plugin_developer,
                    "authenticationToken": self.auth_token
                }
            )
            
            if response:
                if response.get("data", {}).get("authenticated"):
                    print(f"会话认证成功: {response['data']['reason']}")
                    return True
                else:
                    # 检查是否是API错误
                    if response.get("messageType") == "APIError":
                        error_data = response.get("data", {})
                        error_id = error_data.get("errorID")
                        error_message = error_data.get("message", "未知错误")
                        print(f"会话认证失败 [错误ID {error_id}]: {error_message}")
                    else:
                        # 认证失败但不是API错误
                        auth_data = response.get("data", {})
                        reason = auth_data.get("reason", "认证失败")
                        print(f"会话认证失败: {reason}")
            else:
                print("会话认证失败: 未收到响应")
                
            return False
        except Exception as e:
            print(f"会话认证时发生错误: {e}")
            return False
    
    def set_auth_token(self, token, plugin_name, plugin_developer):
        """设置认证令牌
        
        如果已经有保存的认证令牌，可以直接设置它，无需再次请求
        
        参数:
            token (str): 认证令牌
            plugin_name (str): 插件名称
            plugin_developer (str): 插件开发者名称
        """
        self.auth_token = token
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        print("已设置认证令牌")
    
    # ------------------------------
    # 事件相关方法（供用户扩展）
    # ------------------------------
    
    async def empty_event(self):
        """空事件处理方法
        
        这个方法可以作为事件处理方法的模板，用户可以根据需要重写它
        或创建新的事件处理方法来响应VTube Studio发送的各种事件
        """
        pass
    
    async def get_available_models(self):
        """获取可用VTS模型列表
        
        获取VTube Studio中所有可用的模型列表，包括每个模型的基本信息
        
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "AvailableModelsRequest"
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "AvailableModelsResponse",
            "data": {
                "numberOfModels": 2,
                "availableModels": [
                    {
                        "modelLoaded": false,
                        "modelName": "My First Model",
                        "modelID": "UniqueIDToIdentifyThisModelBy1",
                        "vtsModelName": "Model_1.vtube.json",
                        "vtsModelIconName": "ModelIconPNGorJPG_1.png"
                    },
                    {
                        "modelLoaded": true,
                        "modelName": "My Second Model",
                        "modelID": "UniqueIDToIdentifyThisModelBy2",
                        "vtsModelName": "Model_2.vtube.json",
                        "vtsModelIconName": "ModelIconPNGorJPG_1.png"
                    }
                ]
            }
        }
        
        字段解释:
        - numberOfModels: 可用模型总数
        - availableModels: 模型列表，每个模型包含以下信息:
            - modelLoaded: 该模型是否当前已加载到VTube Studio (true/false)
            - modelName: 模型名称
            - modelID: 模型唯一标识符
            - vtsModelName: VTS模型文件名 (相对路径)
            - vtsModelIconName: VTS模型图标文件名 (相对路径，可能为空)
        
        返回:
            dict: 包含可用模型列表的响应数据
        """
        data = {"authenticationToken": self.auth_token} if self.auth_token else {}
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="AvailableModelsRequest",
            data=data
        )
    
    async def get_current_model(self):
        """获取当前加载的模型信息
        
        获取当前在VTube Studio中加载的Live2D模型的详细信息，包括模型名称、ID、加载时间等
        
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "CurrentModelRequest"
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "CurrentModelResponse",
            "data": {
                "modelLoaded": true,
                "modelName": "My Currently Loaded Model",
                "modelID": "UniqueIDToIdentifyThisModelBy",
                "vtsModelName": "Model.vtube.json",
                "vtsModelIconName": "ModelIconPNGorJPG.png",
                "live2DModelName": "Model.model3.json",
                "modelLoadTime": 3021,
                "timeSinceModelLoaded": 419903,
                "numberOfLive2DParameters": 29,
                "numberOfLive2DArtmeshes": 136,
                "hasPhysicsFile": true,
                "numberOfTextures": 2,
                "textureResolution": 4096,
                "modelPosition": {
                    "positionX": -0.1,
                    "positionY": 0.4,
                    "rotation": 9.33,
                    "size": -61.9
                }
            }
        }
        
        字段解释:
        - modelLoaded: 是否加载了模型 (true/false)
        - modelName: 模型名称
        - modelID: 模型唯一标识符
        - vtsModelName: VTS模型文件名 (相对路径)
        - vtsModelIconName: VTS模型图标文件名 (相对路径，可能为空)
        - live2DModelName: Live2D模型文件名 (相对路径)
        - modelLoadTime: 模型加载时间 (毫秒)
        - timeSinceModelLoaded: 模型加载后的时间 (毫秒)
        - numberOfLive2DParameters: Live2D参数数量
        - numberOfLive2DArtmeshes: Live2D网格数量
        - hasPhysicsFile: 是否有物理文件
        - numberOfTextures: 纹理数量
        - textureResolution: 纹理分辨率
        - modelPosition: 模型位置信息
            - positionX: X轴位置
            - positionY: Y轴位置
            - rotation: 旋转角度
            - size: 大小
        
        返回:
            dict: 包含当前模型详细信息的响应数据
        """
        data = {"authenticationToken": self.auth_token} if self.auth_token else {}
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="CurrentModelRequest",
            data=data
        )
    
    async def get_current_model_data(self):
        """获取当前模型的详细数据"""
        data = {"authenticationToken": self.auth_token} if self.auth_token else {}
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="CurrentModelDataRequest",
            data=data
        )
    
    async def get_face_tracking_data(self):
        """获取实时面部跟踪数据"""
        data = {"authenticationToken": self.auth_token} if self.auth_token else {}
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="FaceTrackingDataRequest",
            data=data
        )
    
    async def get_hotkeys(self):
        """获取当前模型的热键列表"""
        data = {"authenticationToken": self.auth_token} if self.auth_token else {}
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="HotkeysInCurrentModelRequest",
            data=data
        )
    
    async def get_statistics(self):
        """获取VTS统计信息
        
        请求VTube Studio当前的运行统计信息，包括:
        - 运行时间(uptime): VTS启动以来的毫秒数
        - 帧率(framerate): 当前渲染帧率
        - VTS版本(vTubeStudioVersion): 当前VTube Studio版本号
        - 允许的插件数(allowedPlugins): 用户允许使用VTS的插件数量
        - 已连接插件数(connectedPlugins): 当前已连接到VTS API的插件数量
        - 是否通过Steam启动(startedWithSteam): VTS是否通过Steam启动
        - 窗口宽度(windowWidth): VTS窗口的像素宽度
        - 窗口高度(windowHeight): VTS窗口的像素高度
        - 是否全屏(windowIsFullscreen): VTS窗口是否处于全屏模式
        
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "StatisticsRequest"
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "StatisticsResponse",
            "data": {
                "uptime": 1439384,
                "framerate": 73,
                "vTubeStudioVersion": "1.9.0",
                "allowedPlugins": 7,
                "connectedPlugins": 2,
                "startedWithSteam": true,
                "windowWidth": 1031,
                "windowHeight": 812,
                "windowIsFullscreen": false
            }
        }
        
        返回:
            dict: 包含统计信息的响应数据
        """
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="StatisticsRequest",
            data={}
        )
    
    async def get_vts_folders(self):
        """获取VTS文件夹信息
        
        返回VTube Studio各种文件夹的名称，这些文件夹位于游戏文件的StreamingAssets文件夹中
        
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "VTSFolderInfoRequest"
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "VTSFolderInfoResponse",
            "data": {
                "models": "Live2DModels",
                "backgrounds": "Backgrounds",
                "items": "Items",
                "config": "Config",
                "logs": "Logs",
                "backup": "Backup"
            }
        }
        
        返回:
            dict: 包含VTS文件夹信息的响应数据，包含以下文件夹:
                - models: Live2D模型文件夹
                - backgrounds: 背景文件夹
                - items: 物品文件夹
                - config: 配置文件夹
                - logs: 日志文件夹
                - backup: 备份文件夹
        """
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="VTSFolderInfoRequest",
            data={}
        )
    
    async def load_model(self, model_id):
        """根据模型ID加载或卸载VTS模型
        
        根据提供的模型ID加载指定模型，或传入空字符串卸载当前加载的模型
        
        参数:
            model_id (str): 要加载的模型ID，如果为空字符串则卸载当前模型
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "ModelLoadRequest",
            "data": {
                "modelID": "UniqueIDOfModelToLoad"
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "ModelLoadResponse",
            "data": {
                "modelID": "UniqueIDOfModelThatWasJustLoaded"
            }
        }
        
        注意事项:
        - 如果应用当前处于无法加载/卸载模型的状态（如打开了配置窗口或已有模型加载操作正在进行），此请求可能会失败并返回错误
        - 此请求有全局2秒冷却时间
        - 如果传入空模型ID，将卸载当前加载的模型（如果没有加载模型则不执行任何操作）
        
        返回:
            dict: 包含加载/卸载结果的响应数据，其中modelID字段表示刚加载的模型ID
        """
        data = {
            "modelID": model_id
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ModelLoadRequest",
            data=data
        )
    async def move_model(self, time_in_seconds, values_are_relative_to_model, 
                        position_x=None, position_y=None, rotation=None, size=None):
        """移动当前加载的VTS模型
        
        更改当前加载模型的位置、旋转和大小
        
        参数:
            time_in_seconds (float): 移动动画的时长（秒），必须是0到2之间的浮点数
            values_are_relative_to_model (bool): 值是否相对于模型当前位置
            position_x (float, optional): X轴位置（-1000到1000之间）
            position_y (float, optional): Y轴位置（-1000到1000之间）
            rotation (float, optional): 旋转角度（-360到360之间）
            size (float, optional): 模型大小（-100到100之间，-100最小，+100最大）
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "MoveModelRequest",
            "data": {
                "timeInSeconds": 0.2,
                "valuesAreRelativeToModel": false,
                "positionX": 0.1,
                "positionY": -0.7,
                "rotation": 16.3,
                "size": -22.5
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "MoveModelResponse",
            "data": {}
        }
        
        功能说明:
        - 如果没有加载模型，将返回错误
        - time_in_seconds为0时，模型会立即移动到目标位置
        - time_in_seconds大于0时，模型会平滑移动到目标位置
        - 在模型移动过程中，用户无法手动拖动模型
        - 可以在当前移动未完成时发送新的移动请求，新请求会中断并替换当前请求
        - 可以通过每帧发送一个time_in_seconds为0的请求来完全控制模型移动
        
        坐标系统:
        - positionX和positionY的值表示模型在屏幕上的位置，[0/0]表示屏幕中心
        - positionX和positionY的取值范围是-1000到1000
        - rotation的值表示旋转角度，顺时针为正，逆时针为负，取值范围是-360到360
        - size的值表示模型大小，-100最小，+100最大
        
        返回:
            dict: 包含移动结果的响应数据
        """
        data = {
            "timeInSeconds": time_in_seconds,
            "valuesAreRelativeToModel": values_are_relative_to_model
        }
        
        # 添加可选参数
        if position_x is not None:
            data["positionX"] = position_x
        if position_y is not None:
            data["positionY"] = position_y
        if rotation is not None:
            data["rotation"] = rotation
        if size is not None:
            data["size"] = size
            
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="MoveModelRequest",
            data=data
        )
    async def get_hotkeys_in_model(self, model_id=None, live2d_item_filename=None):
        """获取当前模型或指定模型的热键列表
        
        获取当前加载模型或指定ID模型的热键列表。
        如果不提供模型ID，则返回当前模型的热键列表。
        如果提供了模型ID，则返回该ID对应模型的热键列表。
        也可以通过提供live2DItemFileName获取特定Live2D项目的热键列表。
        
        参数:
            model_id (str, optional): 模型的唯一ID，不提供则返回当前模型的热键
            live2d_item_filename (str, optional): Live2D项目文件名，用于获取特定项目的热键
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "HotkeysInCurrentModelRequest",
            "data": {
                "modelID": "Optional_UniqueIDOfModel",
                "live2DItemFileName": "Optional_Live2DItemFileName"
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "HotkeysInCurrentModelResponse",
            "data": {
                "modelLoaded": true,
                "modelName": "My Currently Loaded Model",
                "modelID": "UniqueIDOfModel",
                "availableHotkeys": [
                    {
                        "name": "My first hotkey",
                        "type": "ToggleExpression",
                        "description": "Toggles an expression",
                        "file": "myExpression_1.exp3.json",
                        "hotkeyID": "SomeUniqueIdToIdentifyHotkeyWith1",
                        "keyCombination": [],
                        "onScreenButtonID": 8
                    },
                    {
                        "name": "My second hotkey",
                        "type": "TriggerAnimation",
                        "description": "Triggers an animation",
                        "file": "myAnimation.motion3.json",
                        "hotkeyID": "SomeUniqueIdToIdentifyHotkeyWith2",
                        "keyCombination": [],
                        "onScreenButtonID": -1
                    }
                ]
            }
        }
        
        功能说明:
        - 如果不提供modelID且当前未加载任何模型，modelLoaded将为false，availableHotkeys为空数组
        - 如果提供了modelID且未找到对应模型，将返回错误
        - 如果同时提供modelID和live2DItemFileName，仅modelID会被使用
        - file字段：对于TriggerAnimation、ChangeIdleAnimation、ToggleExpression和ChangeVTSModel类型的热键，包含表达式/动画/模型文件名；
          对于ChangeBackground类型的热键，包含不带扩展名的背景名称；对于其他类型，为空字符串
        - description字段：包含热键功能的描述，可以在插件UI中显示
        - keyCombination字段：当前始终为空数组（出于安全原因）
        - onScreenButtonID字段：包含触发热键的屏幕按钮ID（1-8或-1表示未设置）
        
        返回:
            dict: 包含热键列表的响应数据
        """
        data = {}
        
        # 添加可选参数
        if model_id is not None:
            data["modelID"] = model_id
        elif live2d_item_filename is not None:
            data["live2DItemFileName"] = live2d_item_filename
            
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="HotkeysInCurrentModelRequest",
            data=data
        )
    async def trigger_hotkey(self, hotkey_id, item_instance_id=None):
        """触发当前加载模型或指定Live2D项目的热键
        
        触发当前加载的VTube Studio模型的热键，或指定Live2D项目的热键。
        可以通过热键的唯一ID或名称（不区分大小写）来触发。
        如果多个热键具有相同的名称，仅会执行第一个（按UI中显示的顺序）。
        没有名称的热键只能通过ID触发。
        
        参数:
            hotkey_id (str): 要执行的热键的唯一ID或名称
            item_instance_id (str, optional): Live2D项目实例ID，用于在指定项目中触发热键
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "HotkeyTriggerRequest",
            "data": {
                "hotkeyID": "HotkeyNameOrUniqueIdOfHotkeyToExecute",
                "itemInstanceID": "Optional_ItemInstanceIdOfLive2DItemToTriggerThisHotkeyFor"
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "HotkeyTriggerResponse",
            "data": {
                "hotkeyID": "UniqueIdOfHotkeyThatWasExecuted"
            }
        }
        
        功能说明:
        - 如果不提供item_instance_id或留空，将触发当前加载模型的热键
        - 如果提供了item_instance_id，将在指定的Live2D项目中触发热键
        - 热键触发可能会失败的原因：
          * 未找到指定的热键ID或名称
          * 当前没有加载模型
          * 热键冷却时间未结束（同一个热键每5帧只能触发一次）
          * 热键队列已满（队列最多可容纳32个热键）
        - 可以快速连续发送不同的热键，它们会被加入队列，每5帧执行一个
        
        返回:
            dict: 包含执行结果的响应数据
        """
        data = {
            "hotkeyID": hotkey_id
        }
        
        # 添加可选参数
        if item_instance_id is not None:
            data["itemInstanceID"] = item_instance_id
            
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="HotkeyTriggerRequest",
            data=data
        )
    async def trigger_hotkey_item(self):
        pass
    async def get_expression_state(self, details=True, expression_file=None):
        """获取当前模型的表情状态列表
        
        获取当前加载模型中一个或所有表情的状态（激活或未激活）。
        如果提供了expression_file参数，仅会返回该表情的状态。
        如果不提供该参数或留空，将返回当前模型中所有表情的状态。
        
        参数:
            details (bool, optional): 是否返回详细信息。设为true时会返回更多详情（usedInHotkeys和parameters数组），默认为true
            expression_file (str, optional): 表情文件名（如"myExpression_optional_1.exp3.json"），用于获取特定表情的状态
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "ExpressionStateRequest",
            "data": {
                "details": true,
                "expressionFile": "myExpression_optional_1.exp3.json"
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "ExpressionStateResponse",
            "data": {
                "modelLoaded": true,
                "modelName": "My Currently Loaded Model",
                "modelID": "UniqueIDOfModel",
                "expressions": [
                    {
                        "name": "myExpression_optional_1",
                        "file": "myExpression_optional_1.exp3.json",
                        "active": false,
                        "deactivateWhenKeyIsLetGo": false,
                        "autoDeactivateAfterSeconds": false,
                        "secondsRemaining": 0,
                        "usedInHotkeys": [
                            {
                                "name": "Some Hotkey",
                                "id": "SomeUniqueIdToIdentifyHotkeyWith1"
                            }
                        ],
                        "parameters": [
                            {
                                "name": "SomeLive2DParamID",
                                "value": 0
                            }
                        ]
                    }
                ]
            }
        }
        
        功能说明:
        - 如果没有加载模型，expressions数组将为空
        - 如果提供了expressionFile但文件名无效（不以.exp3.json结尾）或在当前模型中未找到，将返回错误
        - details参数设为true时，会返回usedInHotkeys和parameters数组的详细信息；设为false时，这些数组将为空
        - file字段包含表情的完整文件名（如"myExpression_optional_1.exp3.json"）
        - name字段包含表情名称（与file相同，但不包含.exp3.json扩展名）
        - active字段表示表情当前是否激活
        - 如果表情是通过热键激活的，deactivateWhenKeyIsLetGo和autoDeactivateAfterSeconds字段表示热键的相关设置
        
        返回:
            dict: 包含表情状态列表的响应数据
        """
        data = {
            "details": details
        }
        
        # 添加可选参数
        if expression_file is not None:
            data["expressionFile"] = expression_file
            
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ExpressionStateRequest",
            data=data
        )
    async def activate_expression(self, expression_file, active, fade_time=0.25):
        """激活或停用当前模型的表情
        
        直接激活或停用当前加载模型中的特定表情。
        建议通过热键激活表情，以避免用户无法停用没有设置热键的激活表情。
        但如果插件需要，也可以直接激活或停用表情。
        
        参数:
            expression_file (str): 表情文件名（如"myExpression_1.exp3.json"）
            active (bool): 是否激活表情（true表示激活，false表示停用）
            fade_time (float, optional): 淡入/淡出时间（秒），默认值为0.25，实际值会被限制在0-2秒之间
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "ExpressionActivationRequest",
            "data": {
                "expressionFile": "myExpression_1.exp3.json",
                "fadeTime": 0.5,
                "active": true
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "ExpressionActivationResponse",
            "data": {}
        }
        
        功能说明:
        - 如果文件名无效（不以.exp3.json结尾）、在当前模型中未找到或没有加载模型，将返回错误
        - fadeTime参数会被自动限制在0-2秒之间
        - 注意：由于VTS动画系统的限制，淡入时间可以设置，但淡出时间将始终使用与淡入相同的时间
        
        返回:
            dict: 包含操作结果的响应数据
        """
        data = {
            "expressionFile": expression_file,
            "active": active,
            "fadeTime": fade_time
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ExpressionActivationRequest",
            data=data
        )
    async def get_artmesh_list(self):
        """获取当前加载模型中的ArtMesh列表
        
        获取当前加载模型中的ArtMesh名称和标签列表。
        API使用"ArtMesh Name"一词，但实际上指的是ArtMesh ID，该ID在每个模型中都是唯一的（由Live2D Cubism编辑器强制执行）。
        ArtMesh标签可以通过在Live2D编辑器中选择ArtMesh并在UserData字段中输入来添加。
        
        参数:
            无
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "ArtMeshListRequest"
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "ArtMeshListResponse",
            "data": {
                "modelLoaded": true,
                "numberOfArtMeshNames": 5,
                "numberOfArtMeshTags": 2,
                "artMeshNames": ["ArtMesh1", "ArtMesh2", "HairFront1", "HairFront2", "SomeArtMesh"],
                "artMeshTags": ["my_tag", "SomeOtherTag"]
            }
        }
        
        功能说明:
        - 如果没有加载模型，modelLoaded将为false，artMeshNames和artMeshTags数组将为空
        - artMeshTags数组中不会包含重复的标签
        - 标签注意事项：在Live2D编辑器中，您可以在UserData字段中为ArtMesh添加标签。VTube Studio会在空格和换行符处分割文本。
          例如，如果您的标签文本是"my tag"，在VTS中将变成两个标签："my"和"tag"。每个ArtMesh可以添加任意数量的标签
        
        返回:
            dict: 包含ArtMesh列表的响应数据
        """
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ArtMeshListRequest",
            data=data
        )
    async def color_tint(self, colorR, colorG, colorB, colorA, mixWithSceneLightingColor=1.0, 
                       tintAll=False, artMeshNumber=None, nameExact=None, nameContains=None, 
                       tagExact=None, tagContains=None):
        """给匹配条件的ArtMeshes上色
        
        通过提供颜色和匹配条件给ArtMeshes上色。任何匹配给定条件的ArtMesh都将使用给定颜色进行着色。
        要重置ArtMesh颜色，请使用白色（R=G=B=A=255）着色。此请求只能使ArtMesh变暗，不能使其变白。
        
        参数:
            colorR (int): 红色分量值（0-255）
            colorG (int): 绿色分量值（0-255）
            colorB (int): 蓝色分量值（0-255）
            colorA (int): 透明度分量值（0-255）
            mixWithSceneLightingColor (float, optional): 与场景灯光颜色的混合比例（0-1），默认值为1.0
            tintAll (bool, optional): 是否给所有ArtMeshes上色，默认值为False
            artMeshNumber (list, optional): 基于顺序的ArtMesh编号数组，如[1, 3, 5]
            nameExact (list, optional): 精确匹配的ArtMesh名称数组
            nameContains (list, optional): 包含指定字符串的ArtMesh名称数组
            tagExact (list, optional): 精确匹配的ArtMesh标签数组
            tagContains (list, optional): 包含指定字符串的ArtMesh标签数组
            
        请求示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "ColorTintRequest",
            "data": {
                "colorTint": {
                    "colorR": 255,
                    "colorG": 150,
                    "colorB": 0,
                    "colorA": 255,
                    "mixWithSceneLightingColor": 1
                },
                "artMeshMatcher": {
                    "tintAll": false,
                    "artMeshNumber": [1, 3, 5],
                    "nameExact": ["eye_white_left", "eye_white_right"],
                    "nameContains": ["mouth"],
                    "tagExact": [],
                    "tagContains": ["MyTag"]
                }
            }
        }
        
        响应示例:
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "timestamp": 1625405710728,
            "requestID": "SomeID",
            "messageType": "ColorTintResponse",
            "data": {
                "matchedArtMeshes": 3
            }
        }
        
        功能说明:
        - 如果未提供任何颜色值或任何颜色值超出0-255范围，将返回错误
        - 如果在没有加载模型时发送此请求，也将返回错误
        - mixWithSceneLightingColor参数控制着色颜色与场景灯光系统颜色的混合方式：
          * 设为1：提供的颜色值完全覆盖场景灯光设置的值
          * 设为0：场景灯光颜色将覆盖提供的颜色
          * 介于0和1之间：将两种颜色混合
        - 如果场景灯光关闭，mixWithSceneLightingColor参数无效
        - artMeshMatcher对象中的所有数组都是可选的：
          * 如果包含数组，将基于ArtMesh名称或标签是否与给定字符串完全匹配或包含它们来选择ArtMeshes
          * 如果设置tintAll为true，则不给定任何匹配器数组，将给整个模型上色
        - 当会话断开连接时，此会话中已着色的所有ArtMeshes将重置为默认值（完全不透明的白色）
        - 当多个插件/会话覆盖ArtMesh的颜色时，它将具有最近请求设置的颜色
        - 匹配始终不区分大小写
        
        返回:
            dict: 包含着色结果的响应数据，其中matchedArtMeshes字段表示匹配到的ArtMeshes数量
        """
        # 构建colorTint对象
        color_tint_data = {
            "colorR": colorR,
            "colorG": colorG,
            "colorB": colorB,
            "colorA": colorA,
            "mixWithSceneLightingColor": mixWithSceneLightingColor
        }
        
        # 构建artMeshMatcher对象
        art_mesh_matcher = {
            "tintAll": tintAll
        }
        
        # 添加可选的匹配条件
        if artMeshNumber is not None:
            art_mesh_matcher["artMeshNumber"] = artMeshNumber
        if nameExact is not None:
            art_mesh_matcher["nameExact"] = nameExact
        if nameContains is not None:
            art_mesh_matcher["nameContains"] = nameContains
        if tagExact is not None:
            art_mesh_matcher["tagExact"] = tagExact
        if tagContains is not None:
            art_mesh_matcher["tagContains"] = tagContains
        
        # 构建完整的请求数据
        data = {
            "colorTint": color_tint_data,
            "artMeshMatcher": art_mesh_matcher
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ColorTintRequest",
            data=data
        )
    
    async def get_scene_color_overlay_info(self, request_id=None):
        """获取场景光照叠加颜色的信息

        VTube Studio可以将模型与屏幕(Windows/macOS)或特定窗口(仅限Windows)捕获的平均颜色叠加。
        该方法用于获取当前场景光照叠加系统的用户配置和颜色信息。

        参数:
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "SceneColorOverlayInfoRequest"
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "SceneColorOverlayInfoResponse",
                "data": {
                    "active": true,
                    "itemsIncluded": true,
                    "isWindowCapture": false,
                    "baseBrightness": 16,
                    "colorBoost": 35,
                    "smoothing": 6,
                    "colorOverlayR": 206,
                    "colorOverlayG": 150,
                    "colorOverlayB": 153,
                    "colorAvgR": 237,
                    "colorAvgG": 157,
                    "colorAvgB": 162,
                    "leftCapturePart": {
                        "active": true,
                        "colorR": 243,
                        "colorG": 231,
                        "colorB": 234
                    },
                    "middleCapturePart": {
                        "active": true,
                        "colorR": 230,
                        "colorG": 83,
                        "colorB": 89
                    },
                    "rightCapturePart": {
                        "active": false,
                        "colorR": 235,
                        "colorG": 95,
                        "colorB": 101
                    }
                }
            }

        功能说明:
            - active: 表示光照叠加是否开启
            - itemsIncluded: 表示是否所有项目都受光照叠加影响
            - isWindowCapture: true表示捕获窗口颜色，false表示捕获屏幕颜色
            - baseBrightness: 基础亮度(0-100)
            - colorBoost: 颜色增强(0-100)
            - smoothing: 平滑度(0-60)
            - colorAvgR/G/B: 所有激活屏幕部分计算的平均颜色(0-255)
            - colorOverlayR/G/B: 用于叠加ArtMeshes的最终颜色(0-459)
            - left/middle/rightCapturePart: 屏幕各部分的颜色信息

        返回值:
            包含场景光照叠加信息的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="SceneColorOverlayInfoRequest",
            data=data
        )
    async def get_face_found(self, request_id=None):
        """检查当前是否通过活动追踪器找到了人脸

        返回当前是否通过活动追踪器（网络/USB连接的智能手机或网络摄像头追踪器）找到了人脸。

        参数:
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "FaceFoundRequest"
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "FaceFoundResponse",
                "data": {
                    "found": true
                }
            }

        功能说明:
            - 检查当前活动的追踪器（智能手机或网络摄像头）是否检测到人脸
            - 返回结果为布尔值，表示是否找到人脸

        返回值:
            包含人脸检测结果的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="FaceFoundRequest",
            data=data
        )
    async def get_tracking_parameters(self, request_id=None):
        """获取当前在VTube Studio中可用的参数列表

        返回当前在VTube Studio中可用的所有参数列表，包括所有常规参数和插件创建的自定义参数。
        插件创建的参数会被标记为插件创建，并显示创建它们的插件名称。

        重要提示：此请求可能返回大量数据。不建议以高频（60+ FPS）发送此请求，
        因为这可能在较慢的PC上导致性能问题。

        参数:
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "InputParameterListRequest"
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "InputParameterListResponse",
                "data": {
                    "modelLoaded": true,
                    "modelName": "My Currently Loaded Model",
                    "modelID": "UniqueIDOfModel",
                    "customParameters": [
                        {
                            "name": "MyCustomParamName1",
                            "addedBy": "My Plugin Name",
                            "value": 12.4,
                            "min": -30,
                            "max": 30,
                            "defaultValue": 0
                        },
                        {
                            "name": "MyCustomParamName2",
                            "addedBy": "My Plugin Name",
                            "value": 0.833,
                            "min": -10,
                            "max": 10,
                            "defaultValue": 0
                        }
                    ],
                    "defaultParameters": [
                        {
                            "name": "FaceAngleX",
                            "addedBy": "VTube Studio",
                            "value": 45.78,
                            "min": -30,
                            "max": 30,
                            "defaultValue": 0
                        },
                        {
                            "name": "FacePositionX",
                            "addedBy": "VTube Studio",
                            "value": 8.33,
                            "min": -10,
                            "max": 10,
                            "defaultValue": 0
                        }
                    ]
                }
            }

        功能说明:
            - modelLoaded: 表示是否有模型加载
            - modelName: 当前加载的模型名称
            - modelID: 当前加载的模型ID
            - customParameters: 插件创建的自定义参数列表
            - defaultParameters: VTube Studio默认参数列表
            - 每个参数包含name、addedBy、value、min、max和defaultValue属性

        返回值:
            包含可用跟踪参数列表的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="InputParameterListRequest",
            data=data
        )
    async def get_parameter_value(self, parameter_name, request_id=None):
        """获取特定参数的值（默认或自定义）

        获取指定名称的单个参数的当前值和详细信息。
        如果请求的参数不存在，将返回错误。

        参数:
            parameter_name: 要获取的参数名称（区分大小写）
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ParameterValueRequest",
                "data": {
                    "name": "MyCustomParamName1"
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ParameterValueResponse",
                "data": {
                    "name": "MyCustomParamName1",
                    "addedBy": "My Plugin Name",
                    "value": 12.4,
                    "min": -30,
                    "max": 30,
                    "defaultValue": 0
                }
            }

        功能说明:
            - 获取特定参数的当前值、最小值、最大值、默认值和添加者信息
            - 参数名称区分大小写
            - 如果请求的参数不存在，将返回错误

        返回值:
            包含参数详细信息的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "name": parameter_name
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ParameterValueRequest",
            data=data
        )
    async def get_live2d_parameters(self, request_id=None):
        """获取当前模型中所有Live2D参数的值

        获取当前加载的Live2D模型中所有参数的值。
        如果没有加载模型，将返回错误。

        参数:
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "Live2DParameterListRequest"
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "Live2DParameterListResponse",
                "data": {
                    "modelLoaded": true,
                    "modelName": "My Currently Loaded Model",
                    "modelID": "UniqueIDOfModel",
                    "parameters": [
                        {
                            "name": "MyLive2DParameterID1",
                            "value": 12.4,
                            "min": -30,
                            "max": 30,
                            "defaultValue": 0
                        },
                        {
                            "name": "MyLive2DParameterID2",
                            "value": 0,
                            "min": 0,
                            "max": 1,
                            "defaultValue": 0
                        }
                    ]
                }
            }

        功能说明:
            - 获取当前加载模型的所有Live2D参数
            - 如果没有加载模型，modelLoaded将为false，参数数组为空
            - 每个参数包含name、value、min、max和defaultValue属性

        返回值:
            包含Live2D参数列表的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="Live2DParameterListRequest",
            data=data
        )
    async def create_custom_parameter(self, parameter_name, min_value, max_value, default_value, explanation="", request_id=None):
        """添加新的自定义跟踪参数

        添加新的自定义跟踪参数，可在VTube Studio模型中使用。
        添加后，用户可以选择这些参数作为Live2D参数映射的输入。

        参数名称必须是唯一的、字母数字的（不允许空格），长度在4到32个字符之间。
        最小值、最大值和默认值必须是-1000000到1000000之间的浮点数。

        参数:
            parameter_name: 要创建的参数名称
            min_value: 参数的最小值（用于新参数映射的默认下限）
            max_value: 参数的最大值（用于新参数映射的默认上限）
            default_value: 参数的默认值
            explanation: 参数的可选说明（少于256个字符）
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ParameterCreationRequest",
                "data": {
                    "parameterName": "MyNewParamName",
                    "explanation": "This is my new parameter.",
                    "min": -50,
                    "max": 50,
                    "defaultValue": 10
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ParameterCreationResponse",
                "data": {
                    "parameterName": "MyNewParamName"
                }
            }

        功能说明:
            - 创建新的自定义跟踪参数
            - 参数名称必须唯一、字母数字、长度4-32字符
            - 如果参数名称无效或已存在（由其他插件创建），请求将失败
            - 同一个插件可以多次创建同名参数，覆盖min、max和defaultValue
            - 全局限制300个自定义参数，每个插件限制100个
            - 撤销插件认证令牌时，该插件创建的所有自定义参数将被删除

        返回值:
            包含创建的参数名称的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "parameterName": parameter_name,
            "explanation": explanation,
            "min": min_value,
            "max": max_value,
            "defaultValue": default_value
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ParameterCreationRequest",
            data=data
        )
    async def delete_custom_parameter(self, parameter_name, request_id=None):
        """删除自定义参数

        删除指定的自定义参数。默认参数不能删除，也不能删除由其他插件创建的参数。
        只能删除使用当前会话认证的插件创建的自定义参数。

        参数:
            parameter_name: 要删除的参数名称
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ParameterDeletionRequest",
                "data": {
                    "parameterName": "MyNewParamName"
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ParameterDeletionResponse",
                "data": {
                    "parameterName": "MyNewParamName"
                }
            }

        功能说明:
            - 删除自定义参数
            - 只能删除由当前认证插件创建的自定义参数
            - 默认参数和其他插件创建的参数无法删除

        返回值:
            包含删除的参数名称的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "parameterName": parameter_name
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ParameterDeletionRequest",
            data=data
        )
    async def inject_parameter_data(self, parameter_values, face_found=False, mode="set", request_id=None):
        """向默认或自定义参数注入数据

        向任何默认或自定义参数注入数据。这些跟踪参数将用作加载的VTube Studio模型和
        任何加载的Live2D项目的输入。

        参数值必须是-1000000到1000000之间的浮点数。如果参数不存在，将返回错误。
        如果摄像头/iOS/Android跟踪存在这些参数的值，API的值将覆盖这些值，
        但您必须至少每秒重新发送一次数据，否则参数将返回到之前的控制方式。

        参数:
            parameter_values: 参数值列表，每个参数包含id和value，可选weight
            face_found: 可选，是否检测到面部，默认False
            mode: 可选，操作模式，默认"set"
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "InjectParameterDataRequest",
                "data": {
                    "faceFound": false,
                    "mode": "set",
                    "parameterValues": [
                        {
                            "id": "FaceAngleX",
                            "value": 12.31
                        },
                        {
                            "id": "MyNewParamName",
                            "weight": 0.8,
                            "value": 0.7
                        }
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "InjectParameterDataResponse",
                "data": {}
            }

        功能说明:
            - 向默认或自定义参数注入数据
            - 值必须是-1000000到1000000之间的浮点数
            - 如果参数不存在，将返回错误
            - 必须至少每秒重新发送一次数据，否则参数将返回到之前的控制方式
            - 可选的weight参数(0-1)可用于混合API值和面部跟踪值
            - face_found参数可用于控制"跟踪丢失"动画的播放
            - 多插件控制同一参数:
              - 默认模式("set")下，只有一个插件可以同时控制一个参数
              - 如果另一个插件已经在使用"set"模式控制该参数，将返回错误
              - 可以将mode设置为"add"，允许多个插件同时向同一参数添加值
              - "add"模式下，weight参数将被忽略
            - 模式说明:
              - "set": 覆盖参数当前值(默认模式)
              - "add": 将提供的值添加到参数当前值上
            - "add"模式适用于bonk/throwing类型的插件等场景

        返回值:
            空响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "faceFound": face_found,
            "mode": mode,
            "parameterValues": parameter_values
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="InjectParameterDataRequest",
            data=data
        )
    async def get_current_model_physics(self, request_id=None):
        """获取当前加载的VTS模型的物理设置

        获取当前加载模型的物理设置，包括基础物理强度、基础风力强度以及每个物理组的物理和风力乘数。

        参数:
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "GetCurrentModelPhysicsRequest"
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "GetCurrentModelPhysicsResponse",
                "data": {
                    "modelLoaded": true,
                    "modelName": "My Currently Loaded Model",
                    "modelID": "UniqueIDOfModel",
                    "modelHasPhysics": true,
                    "physicsSwitchedOn": true,
                    "usingLegacyPhysics": false,
                    "physicsFPSSetting": -1,
                    "baseStrength": 50,
                    "baseWind": 17,
                    "apiPhysicsOverrideActive": false,
                    "apiPhysicsOverridePluginName": "",
                    "physicsGroups": [
                        {
                            "groupID": "PhysicsSetting1",
                            "groupName": "Hair Front Physics",
                            "strengthMultiplier": 1.5,
                            "windMultiplier": 0.3
                        },
                        {
                            "groupID": "PhysicsSetting2",
                            "groupName": "Clothes Physics",
                            "strengthMultiplier": 1,
                            "windMultiplier": 2
                        }
                    ]
                }
            }

        功能说明:
            - 获取当前加载模型的物理设置
            - 如果没有加载模型，modelLoaded将为false，physicsGroups数组为空
            - 模型物理设置包括：
              - baseStrength: 基础物理强度(0-100，默认50)
              - baseWind: 基础风力强度(0-100，默认0)
              - physicsGroups: 每个物理组的设置，包括：
                - strengthMultiplier: 物理乘数(0-2，默认1)
                - windMultiplier: 风力乘数(0-2，默认1)
            - 其他信息：
              - modelHasPhysics: 模型是否有有效的物理设置
              - physicsSwitchedOn: 用户是否为该模型激活了"使用物理"开关
              - usingLegacyPhysics: "传统物理"开关的状态
              - physicsFPSSetting: 物理FPS设置(30, 60, 120或-1表示使用应用程序相同的FPS)
              - apiPhysicsOverrideActive: 是否有插件正在覆盖物理设置
              - apiPhysicsOverridePluginName: 覆盖物理设置的插件名称

        返回值:
            包含当前加载模型物理设置的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {}
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="GetCurrentModelPhysicsRequest",
            data=data
        )
    async def set_current_model_physics(self, strength_overrides=None, wind_overrides=None, request_id=None):
        """覆盖当前加载的VTS模型的物理设置

        覆盖当前加载模型的物理设置。一旦一个插件通过此API控制了物理系统，
        其他插件在第一个插件放弃控制之前无法使用此API。

        物理/风力乘数应该在0到2之间，物理/风力基值应该是0到100之间的整数。
        覆盖值在overrideSeconds时间后自动失效，需要重复发送请求以保持覆盖。
        覆盖计时器值必须在0.5到5秒之间。

        参数:
            strength_overrides: 物理强度覆盖列表
            wind_overrides: 风力覆盖列表
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "SetCurrentModelPhysicsRequest",
                "data": {
                    "strengthOverrides": [
                        {
                            "id": "PhysicsSetting1",
                            "value": 1.5,
                            "setBaseValue": false,
                            "overrideSeconds": 2
                        }
                    ],
                    "windOverrides": [
                        {
                            "id": "",
                            "value": 85,
                            "setBaseValue": true,
                            "overrideSeconds": 5
                        }
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "SetCurrentModelPhysicsResponse",
                "data": {}
            }

        功能说明:
            - 覆盖当前加载模型的物理设置
            - 一旦一个插件控制了物理系统，其他插件无法使用此API
            - 如果没有加载模型、提供的物理组ID不存在或忘记添加覆盖值，将返回错误
            - 物理/风力乘数应该在0到2之间，物理/风力基值应该是0到100之间的整数
            - 覆盖值在overrideSeconds时间后自动失效，需要重复发送请求以保持覆盖
            - 覆盖计时器值必须在0.5到5秒之间
            - strength_overrides和wind_overrides参数结构:
              - id: 物理组ID，如果setBaseValue为true则可以为空
              - value: 覆盖值
              - setBaseValue: 是否设置基值，如果为true则设置全局基值，否则设置特定物理组的值
              - overrideSeconds: 覆盖持续时间(0.5-5秒)

        返回值:
            空响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "strengthOverrides": strength_overrides or [],
            "windOverrides": wind_overrides or []
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="SetCurrentModelPhysicsRequest",
            data=data
        )
    async def ndi_config(self, set_new_config=False, ndi_active=None, use_ndi5=None, use_custom_resolution=None, custom_width_ndi=-1, custom_height_ndi=-1, request_id=None):
        """获取和设置NDI设置

        请求当前NDI设置并可以通过API更改它们。这允许您打开/关闭NDI，
        设置自定义固定分辨率等。

        如果将set_new_config设置为false，则只返回当前配置，忽略其他所有字段。
        如果设置为true，则会设置给定的配置（如果有效）。

        参数:
            set_new_config: 是否设置新配置，默认False
            ndi_active: 可选，打开/关闭NDI
            use_ndi5: 可选，使用NDI 5而不是NDI 4
            use_custom_resolution: 可选，使用自定义分辨率
            custom_width_ndi: 可选，自定义宽度(256-8192，必须是16的倍数)，-1表示不更改
            custom_height_ndi: 可选，自定义高度(256-8192，必须是8的倍数)，-1表示不更改
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "NDIConfigRequest",
                "data": {
                    "setNewConfig": true,
                    "ndiActive": true,
                    "useNDI5": true,
                    "useCustomResolution": true,
                    "customWidthNDI": 1024,
                    "customHeightNDI": 512
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "NDIConfigResponse",
                "data": {
                    "setNewConfig": true,
                    "ndiActive": true,
                    "useNDI5": true,
                    "useCustomResolution": true,
                    "customWidthNDI": 1024,
                    "customHeightNDI": 512
                }
            }

        功能说明:
            - 获取和设置NDI设置
            - ndiActive: 打开/关闭NDI
            - useNDI5: 使用NDI 5而不是NDI 4
            - useCustomResolution: 使用自定义分辨率
            - customWidthNDI: 自定义宽度(256-8192，必须是16的倍数)
            - customHeightNDI: 自定义高度(256-8192，必须是8的倍数)
            - 将customWidthNDI和customHeightNDI都设置为-1可跳过设置分辨率
            - API有3秒的冷却期，过于频繁调用会返回错误

        返回值:
            包含当前NDI设置的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "setNewConfig": set_new_config
        }
        
        # 仅在setNewConfig为true时添加其他参数
        if set_new_config:
            if ndi_active is not None:
                data["ndiActive"] = ndi_active
            if use_ndi5 is not None:
                data["useNDI5"] = use_ndi5
            if use_custom_resolution is not None:
                data["useCustomResolution"] = use_custom_resolution
            if custom_width_ndi != -1:
                data["customWidthNDI"] = custom_width_ndi
            if custom_height_ndi != -1:
                data["customHeightNDI"] = custom_height_ndi
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="NDIConfigRequest",
            data=data
        )
    async def item_list_request(self, include_available_spots=False, include_item_instances_in_scene=False, 
                              include_available_item_files=False, only_items_with_file_name=None, 
                              only_items_with_instance_id=None, request_id=None):
        """获取场景中的项目列表、可用项目文件和加载位置

        该请求允许获取当前场景中的项目列表、用户PC上可用加载的项目文件列表（包括Live2D项目、动画文件夹等），
        以及当前可用于加载项目的位置列表。

        参数:
            include_available_spots: 是否包含当前可用于加载项目的位置列表
            include_item_instances_in_scene: 是否包含当前场景中已加载的项目实例列表
            include_available_item_files: 是否包含可用加载的项目文件列表（可能会导致应用短暂卡顿，建议谨慎使用）
            only_items_with_file_name: 可选，仅返回指定文件名的项目
            only_items_with_instance_id: 可选，仅返回指定实例ID的项目
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemListRequest",
                "data": {
                    "includeAvailableSpots": true,
                    "includeItemInstancesInScene": true,
                    "includeAvailableItemFiles": false,
                    "onlyItemsWithFileName": "OPTIONAL_my_item_filename.png",
                    "onlyItemsWithInstanceID": "OPTIONAL_InstanceIdOfItemInScene"
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "messageType": "ItemListResponse",
                "requestID": "SomeID",
                "data": {
                    "itemsInSceneCount": 2,
                    "totalItemsAllowedCount": 60,
                    "canLoadItemsRightNow": true,
                    "availableSpots": [
                        -30,-29,-28,-27,-26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,
                        -13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1,3,4,5,6,7,8,9,10,11,12,13,
                        14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30
                    ],
                    "itemInstancesInScene": [
                        {
                            "fileName": "Ribbon (@denchisoft)",
                            "instanceID": "18de53dc47154b00afdd382a6ebd2194",
                            "order": 1,
                            "type": "Live2D",
                            "censored": false,
                            "flipped": false,
                            "locked": false,
                            "smoothing": 0.0,
                            "framerate": 0.0,
                            "frameCount": -1,
                            "currentFrame": -1,
                            "pinnedToModel": true,
                            "pinnedModelID": "47c71722c5304a039b0570b60a189875",
                            "pinnedArtMeshID": "D_FACE_00",
                            "groupName": "",
                            "sceneName": "",
                            "fromWorkshop": false
                        },
                        {
                            "fileName": "akari_fly (@walfieee)",
                            "instanceID": "716cddf2e12a438ab5da05bbbf8b341c",
                            "order": 2,
                            "type": "AnimationFolder",
                            "censored": false,
                            "flipped": false,
                            "locked": false,
                            "smoothing": 0.0,
                            "framerate": 15.0,
                            "frameCount": 7,
                            "currentFrame": 0,
                            "pinnedToModel": false,
                            "pinnedModelID": "",
                            "pinnedArtMeshID": "",
                            "groupName": "",
                            "sceneName": "",
                            "fromWorkshop": false
                        }
                    ],
                    "availableItemFiles": [
                        {
                            "fileName": "Ribbon (@denchisoft)",
                            "type": "Live2D",
                            "loadedCount": 1
                        },
                        {
                            "fileName": "ANIM_headpat",
                            "type": "AnimationFolder",
                            "loadedCount": 0
                        },
                        {
                            "fileName": "workshop_2801215328_ANIM_loading gif",
                            "type": "AnimationFolder",
                            "loadedCount": 0
                        },
                        {
                            "fileName": "akari_fly (@walfieee)",
                            "type": "AnimationFolder",
                            "loadedCount": 1
                        },
                        {
                            "fileName": "b_woozy (@denchisoft).png",
                            "type": "PNG",
                            "loadedCount": 0
                        }
                    ]
                }
            }

        功能说明:
            - 项目类型包括: PNG, JPG, GIF, AnimationFolder, Live2D 或 Unknown
            - 文件名是唯一的，区分大小写
            - 实例ID在场景中是唯一的
            - "includeAvailableItemFiles"设为true会读取用户PC上的完整项目文件列表，可能会导致应用短暂卡顿
            - "canLoadItemsRightNow"为false表示当前无法加载项目（如用户打开了特定菜单或对话框）
            - "itemsInSceneCount"表示当前场景中的项目数量
            - "totalItemsAllowedCount"表示场景中允许加载的最大项目数量
            - "availableSpots"数组包含当前可用于加载项目的位置编号
            - "itemInstancesInScene"数组包含场景中已加载的项目实例详细信息
            - "availableItemFiles"数组包含可用加载的项目文件信息

        返回值:
            包含场景项目列表、可用项目文件和加载位置的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "includeAvailableSpots": include_available_spots,
            "includeItemInstancesInScene": include_item_instances_in_scene,
            "includeAvailableItemFiles": include_available_item_files
        }
        
        # 添加可选的过滤参数
        if only_items_with_file_name is not None:
            data["onlyItemsWithFileName"] = only_items_with_file_name
        if only_items_with_instance_id is not None:
            data["onlyItemsWithInstanceID"] = only_items_with_instance_id
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemListRequest",
            data=data
        )
    async def item_load_request(self, file_name, position_x=0.0, position_y=0.0, size=0.32, rotation=0.0, 
                              fade_time=0.0, order=0, fail_if_order_taken=False, smoothing=0.0, 
                              censored=False, flipped=False, locked=False, unload_when_plugin_disconnects=True, 
                              custom_data_base64="", custom_data_ask_user_first=True, 
                              custom_data_skip_asking_user_if_whitelisted=True, custom_data_ask_timer=-1, 
                              request_id=None):
        """将项目加载到场景中

        该请求允许将项目加载到场景中，包括从用户PC的"Items"文件夹加载项目，
        以及加载自定义数据项（base64编码的PNG/JPG/GIF文件）。

        参数:
            file_name: 项目文件名（从Items文件夹加载）或自定义数据项的文件名
            position_x: X轴位置，范围-1000到1000（-1/1为屏幕边缘）
            position_y: Y轴位置，范围-1000到1000（-1/1为屏幕边缘）
            size: 项目大小，范围0到1（0.32为默认大小）
            rotation: 项目旋转角度（度）
            fade_time: 淡入淡出时间，范围0到2秒
            order: 项目在场景中的排序顺序
            fail_if_order_taken: 如果请求的顺序已被占用，是否加载失败
            smoothing: 平滑度，范围0到1
            censored: 是否启用审查模式
            flipped: 是否水平翻转
            locked: 是否锁定项目
            unload_when_plugin_disconnects: 当插件断开连接时是否卸载项目
            custom_data_base64: 自定义数据项的base64编码（PNG/JPG/GIF），空表示从Items文件夹加载
            custom_data_ask_user_first: 是否在加载自定义数据项前询问用户
            custom_data_skip_asking_user_if_whitelisted: 如果项目在白名单中，是否跳过询问用户
            custom_data_ask_timer: 询问用户的超时时间（秒），-1表示不超时
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemLoadRequest",
                "data": {
                    "fileName": "some_item_name.jpg",
                    "positionX": 0,
                    "positionY": 0.5,
                    "size": 0.33,
                    "rotation": 90,
                    "fadeTime": 0.5,
                    "order": 4,
                    "failIfOrderTaken": false,
                    "smoothing": 0,
                    "censored": false,
                    "flipped": false,
                    "locked": false,
                    "unloadWhenPluginDisconnects": true,
                    "customDataBase64": "",
                    "customDataAskUserFirst": true,
                    "customDataSkipAskingUserIfWhitelisted": true,
                    "customDataAskTimer": -1
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ItemLoadResponse",
                "data": {
                    "instanceID": "SomeUniqueItemInstanceId",
                    "fileName": "some_item_name.jpg"
                }
            }

        功能说明:
            - 项目可以从用户PC的"Items"文件夹加载，或通过base64编码的自定义数据加载
            - 位置、大小、旋转等属性可以自定义设置
            - 支持淡入淡出效果、排序顺序控制
            - 自定义数据项需要LoadCustomImagesAsItems权限
            - 自定义数据项的尺寸必须在64-2048像素之间，数据大小小于5MB
            - 自定义数据项的文件名必须是字母数字（可包含连字符），以.jpg/.png/.gif结尾
            - 响应包含新加载项目的实例ID和文件名
            - 如果加载自定义数据项，文件名会由VTube Studio生成，与传入的可能不同
            - 可以使用生成的文件名再次请求加载该项目，无需传递自定义数据
            - 自定义数据项在VTube Studio重启后会被清除（临时文件）

        返回值:
            包含新加载项目实例ID和文件名的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "fileName": file_name,
            "positionX": position_x,
            "positionY": position_y,
            "size": size,
            "rotation": rotation,
            "fadeTime": fade_time,
            "order": order,
            "failIfOrderTaken": fail_if_order_taken,
            "smoothing": smoothing,
            "censored": censored,
            "flipped": flipped,
            "locked": locked,
            "unloadWhenPluginDisconnects": unload_when_plugin_disconnects,
            "customDataBase64": custom_data_base64,
            "customDataAskUserFirst": custom_data_ask_user_first,
            "customDataSkipAskingUserIfWhitelisted": custom_data_skip_asking_user_if_whitelisted,
            "customDataAskTimer": custom_data_ask_timer
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemLoadRequest",
            data=data
        )
    async def item_unload_request(self, unload_all_in_scene=False, unload_all_loaded_by_this_plugin=False,
                               allow_unloading_items_loaded_by_user_or_other_plugins=True, instance_ids=None,
                               file_names=None, request_id=None):
        """从场景中卸载项目

        该请求允许卸载场景中当前加载的任何项目。
        设置 "unloadAllInScene" 为 true 会卸载所有项目，此时其他所有字段将被忽略。
        设置 "unloadAllLoadedByThisPlugin" 为 true 会卸载该插件加载的所有项目。
        如果要防止卸载用户或其他插件加载的项目，将 "allowUnloadingItemsLoadedByUserOrOtherPlugins" 设置为 false。
        也可以通过 "instance_ids" 和 "file_names" 数组请求特定项目实例或从特定文件名加载的项目实例。
        即使在场景中找不到这些项目，请求也不会返回错误，只会返回一个空数组的响应。
        如果用户当前打开了防止VTS加载/卸载项目的菜单，可能会返回 CannotCurrentlyUnloadItem 类型的错误。

        参数:
            unload_all_in_scene: 是否卸载场景中的所有项目
            unload_all_loaded_by_this_plugin: 是否卸载该插件加载的所有项目
            allow_unloading_items_loaded_by_user_or_other_plugins: 是否允许卸载用户或其他插件加载的项目
            instance_ids: 要卸载的项目实例ID数组
            file_names: 要卸载的项目文件名数组
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemUnloadRequest",
                "data": {
                    "unloadAllInScene": false,
                    "unloadAllLoadedByThisPlugin": false,
                    "allowUnloadingItemsLoadedByUserOrOtherPlugins": true,
                    "instanceIDs": [
                        "SomeInstanceIdOfItemToUnload", "SomeOtherInstanceIdOfItemToUnload"
                    ],
                    "fileNames": [
                        "UnloadAllItemInstancesWithThisFileName", "SomeOtherFileName"
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ItemUnloadResponse",
                "data": {
                    "unloadedItems": [
                        {
                            "instanceID": "SomeInstanceId",
                            "fileName": "SomeFileName"
                        },
                        {
                            "instanceID": "SomeOtherInstanceId",
                            "fileName": "SomeFileName"
                        }
                    ]
                }
            }

        返回值:
            包含卸载项目的实例ID和文件名的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "unloadAllInScene": unload_all_in_scene,
            "unloadAllLoadedByThisPlugin": unload_all_loaded_by_this_plugin,
            "allowUnloadingItemsLoadedByUserOrOtherPlugins": allow_unloading_items_loaded_by_user_or_other_plugins
        }
        
        # 处理可选参数
        if instance_ids is not None:
            data["instanceIDs"] = instance_ids
        if file_names is not None:
            data["fileNames"] = file_names
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemUnloadRequest",
            data=data
        )
    async def item_animation_control_request(self, item_instance_id, framerate=-1, frame=-1, 
                                          brightness=-1, opacity=-1, set_auto_stop_frames=False,
                                          auto_stop_frames=None, set_animation_play_state=False,
                                          animation_play_state=True, request_id=None):
        """控制场景中项目的动画和属性

        该请求允许控制场景中项目的某些方面，包括使项目变暗（黑色叠加）、改变不透明度，
        以及控制动画项目的动画。此请求不适用于Live2D项目，如果尝试会返回ItemAnimationControlUnsupportedItemType错误。
        对于动画项目，可以设置帧率（单位：帧/秒，会自动限制在0.1到120之间）。
        也可以使用"frame"字段手动使动画跳转到特定帧。如果帧索引无效，会返回错误。
        如果不想更改帧率、当前帧、亮度或不透明度，可以为这些字段传入-1（如果从负载中省略这些字段，默认也是-1）。
        可以使用"animationPlayState"字段启动/停止动画（true=播放动画，false=停止动画）。
        只有当"setAnimationPlayState"设置为true时，才会使用此字段，否则不会更改动画播放状态。

        使用自动停止帧：
        可以使用"autoStopFrames"数组设置动画将自动停止播放的帧索引列表。
        只有当"setAutoStopFrames"设置为true时，才会使用此数组，否则不会更改自动停止帧。
        如果要删除自动停止帧，请将"setAutoStopFrames"设置为true，并在"autoStopFrames"中设置一个空数组。
        最多可以有1024个自动停止帧。
        一旦动画到达这些帧之一，它将停止播放，只能通过API再次使用此请求将动画播放状态设置为true来启动。

        参数:
            item_instance_id: 项目实例ID
            framerate: 帧率（-1表示不改变）
            frame: 当前帧（-1表示不改变）
            brightness: 亮度（-1表示不改变）
            opacity: 不透明度（-1表示不改变）
            set_auto_stop_frames: 是否设置自动停止帧
            auto_stop_frames: 自动停止帧数组
            set_animation_play_state: 是否设置动画播放状态
            animation_play_state: 动画播放状态
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemAnimationControlRequest",
                "data": {
                    "itemInstanceID": "ItemInstanceId",
                    "framerate": 12,
                    "frame": 3,
                    "brightness": 1,
                    "opacity": 1,
                    "setAutoStopFrames": true,
                    "autoStopFrames": [0, 7, 26],
                    "setAnimationPlayState": true,
                    "animationPlayState": true
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ItemAnimationControlResponse",
                "data": {
                    "frame": 3,
                    "animationPlaying": true
                }
            }

        返回值:
            包含当前帧索引和动画是否正在播放的响应数据（仅适用于动画项目）
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "itemInstanceID": item_instance_id,
            "framerate": framerate,
            "frame": frame,
            "brightness": brightness,
            "opacity": opacity,
            "setAutoStopFrames": set_auto_stop_frames,
            "setAnimationPlayState": set_animation_play_state,
            "animationPlayState": animation_play_state
        }
        
        # 处理可选参数
        if auto_stop_frames is not None:
            data["autoStopFrames"] = auto_stop_frames
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemAnimationControlRequest",
            data=data
        )
    async def item_move_request(self, items_to_move, request_id=None):
        """在场景中移动项目

        该请求允许在场景中移动项目。通过填充"itemsToMove"数组来指定要移动的所有项目。
        响应数组("movedItems")将包含每个请求项目的一个条目，显示移动请求是否成功(请参见各自的"success"字段)。
        如果不成功，"errorID"字段将包含错误代码，告诉您出了什么问题。如果成功，"success"将为true，"errorID"将为-1。

        "itemsToMove"数组最多可以有64个条目。超出的所有条目都将被忽略。
        如果数组中有重复的项目实例ID条目，则将使用该ID的数组中的最后一个条目。
        如果要立即设置位置(例如，当您想每帧设置一个新位置时)，将"timeInSeconds"设置为0。
        否则，您可以使用此字段设置用于移动淡入淡出的时间(限制在0到30秒之间)。

        如果要设置项目的翻转状态，请将"setFlip"设置为true。然后可以使用"flip"字段设置翻转状态。
        如果要更改项目的顺序，可以使用"order"字段。您只能将顺序更改为未被占用的顺序位置(请参阅ItemListResponse)。
        如果不想更改顺序，请将此字段设置为-1000或更低，或者您可以将其设置为项目的当前顺序值。
        此外，当用户打开任何配置窗口时，您无法更改顺序。
        顺序不会像其他参数那样淡入淡出(如果请求的话)，而是在收到请求后立即更改为请求的值。

        对于设置移动目标的字段("positionX"、"positionY"、"size"和"rotation")，请参考ItemLoadRequest的文档。
        唯一的区别是，这个ItemMoveRequest不会在给定值过高/过低时返回错误。
        相反，如果您希望忽略相应的字段，可以设置-1000或更低的值。
        如果这样做，该字段将不包括在移动中，而是使用相应的当前值。

        项目移动过渡淡入淡出类型:
        可以使用"fadeMode"字段设置位置/旋转/大小淡入淡出的移动类型。
        接受的值为"linear"、"easeIn"、"easeOut"、"easeBoth"、"overshoot"和"zip"。
        它们只会在"timeInSeconds"字段设置为大于0时使用。

        如果您希望用户能够在项目移动时通过点击/拖动来停止移动，请将"userCanStop"设置为true。
        如果将其设置为false，用户将无法在移动过程中与项目进行交互。

        参数:
            items_to_move: 要移动的项目列表，每个项目包含以下字段:
                - itemInstanceID: 项目实例ID
                - timeInSeconds: 移动时间(秒)，范围0-30，0表示立即移动
                - fadeMode: 移动过渡类型(linear, easeIn, easeOut, easeBoth, overshoot, zip)
                - positionX: X轴位置，-1000或更低表示不改变
                - positionY: Y轴位置，-1000或更低表示不改变
                - size: 项目大小，-1000或更低表示不改变
                - rotation: 项目旋转角度(度)，-1000或更低表示不改变
                - order: 项目排序顺序，-1000或更低表示不改变
                - setFlip: 是否设置翻转状态
                - flip: 翻转状态
                - userCanStop: 用户是否可以停止移动
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemMoveRequest",
                "data": {
                    "itemsToMove": [
                        {
                            "itemInstanceID": "ItemInstanceId",
                            "timeInSeconds": 1,
                            "fadeMode": "easeOut",
                            "positionX": 0.2,
                            "positionY": -0.8,
                            "size": 0.6,
                            "rotation": 180,
                            "order": -1000,
                            "setFlip": true,
                            "flip": false,
                            "userCanStop": true
                        },
                        {
                            "itemInstanceID": "SomeOther_ItemInstanceId",
                            "timeInSeconds": 0.5,
                            "fadeMode": "zip",
                            "positionX": 1,
                            "positionY": 1,
                            "size": 0.3,
                            "rotation": 0,
                            "order": 25,
                            "setFlip": false,
                            "flip": false,
                            "userCanStop": false
                        }
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ItemMoveResponse",
                "data": {
                    "movedItems": [
                        {
                            "itemInstanceID": "ItemInstanceId",
                            "success": true,
                            "errorID": -1
                        },
                        {
                            "itemInstanceID": "SomeOther_ItemInstanceId",
                            "success": false,
                            "errorID": 900
                        }
                    ]
                }
            }

        返回值:
            包含每个请求项目移动结果的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "itemsToMove": items_to_move
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemMoveRequest",
            data=data
        )
    async def art_mesh_selection_request(self, requested_art_mesh_count=-1, active_art_meshes=None,
                                       text_override="", help_override="", request_id=None):
        """让用户选择ArtMeshes

        该请求用于在VTube Studio中显示当前加载的主要Live2D模型的所有ArtMeshes列表，
        并让用户选择一个或多个ArtMeshes。一旦用户完成选择，将返回ArtMesh IDs。
        您可以在各种其他API请求中使用这些ArtMesh IDs，例如为它们应用颜色色调或使它们不可见。

        如果当前没有加载模型或当前打开了其他窗口，请求将返回错误。
        用户可以将鼠标悬停在ArtMeshes上以显示其ID，并单击它们以过滤显示列表中位于单击位置的所有ArtMeshes。

        使用requested_art_mesh_count字段指定用户必须激活多少个ArtMeshes。
        直到恰好激活了那么多ArtMeshes，"确定"按钮才会可用。如果将requested_art_mesh_count设置为0或更低，
        系统将要求用户选择任意数量的ArtMeshes(但至少一个)。

        如果要在列表中预激活ArtMeshes，可以使用active_art_meshes列表并传入一些ArtMesh IDs。
        如果这些ID中的任何一个不包含在当前模型中，将返回错误。如果您想要当前加载模型中所有ArtMeshes的列表，
        请使用ArtMeshListRequest。

        列表中有一些默认文本，要求用户为插件选择ArtMeshes。当您按下右上角的"?"按钮(帮助)时，
        会在弹出窗口中显示相同的文本。您可以使用text_override和help_override字段分别覆盖这两个字符串。
        如果将它们留空(空字符串)、null或从负载中省略它们，将使用上面显示的默认消息。
        如果要覆盖这些消息，您提供的字符串必须在4到1024个字符之间，否则将使用默认值。
        您可以在提供的字符串中使用\\n表示换行。

        参数:
            requested_art_mesh_count: 用户必须选择的ArtMeshes数量，0或更低表示任意数量
            active_art_meshes: 预激活的ArtMesh IDs列表
            text_override: 覆盖显示在ArtMesh选择列表上方的文本
            help_override: 覆盖用户按下?按钮时显示的文本
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ArtMeshSelectionRequest",
                "data": {
                    "textOverride": "This text is shown over the ArtMesh selection list.",
                    "helpOverride": "This text is shown when the user presses the ? button.",
                    "requestedArtMeshCount": 5,
                    "activeArtMeshes": [
                        "D_BODY_00",
                        "D_ARM_R_05"
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ArtMeshSelectionResponse",
                "data": {
                    "success": true,
                    "activeArtMeshes": [
                        "D_BROW_00",
                        "D_EYE_BALL_03",
                        "D_EYE_BALL_02",
                        "D_EYE_BALL_01",
                        "D_EYE_BALL_00",
                        "D_EYE_11"
                    ],
                    "inactiveArtMeshes": [
                        "D_EAR_06",
                        "D_BODY_00",
                        "D_ARM_R_05"
                    ]
                }
            }

        返回值:
            包含用户选择结果的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "requestedArtMeshCount": requested_art_mesh_count,
            "textOverride": text_override,
            "helpOverride": help_override
        }
        
        # 处理可选参数
        if active_art_meshes is not None:
            data["activeArtMeshes"] = active_art_meshes
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ArtMeshSelectionRequest",
            data=data
        )
    async def item_pin_request(self, pin, item_instance_id, angle_relative_to="RelativeToModel",
                             size_relative_to="RelativeToWorld", vertex_pin_type="Provided",
                             pin_info=None, request_id=None):
        """将项目固定到模型上

        该请求用于将场景中的项目固定到当前加载的模型上。
        必须在itemInstanceID字段中指定用于标识项目的项目实例ID。
        如果要取消固定项目，只需将pin设置为false。在这种情况下，不需要提供其他信息。
        如果当前没有加载具有该ID的项目，将返回错误。

        如果要固定项目，必须在pinInfo对象中提供固定位置。有多种固定项目的方法。
        例如，可以提供一个精确的位置来固定，或者可以只提供一个ArtMesh，让VTS将项目固定到它的中心或ArtMesh上的随机位置。

        当您为一个具有活动ItemMoveRequest的项目发送ItemPinRequest时，ItemMoveRequest将被自动取消。
        此外，如果您的插件(或多个不同的插件)对同一个项目发送多个固定请求，
        并且它们在VTS的同一帧中被接收，则只有最后一个接收到的固定请求会被执行，而其他的则会被丢弃。

        固定选项：
        有三个字段决定了pinInfo中提供的数据如何被解释：
        
        angleRelativeTo: 如何解释提供的角度？
        - RelativeToWorld: 绝对角度。如果传入0作为角度，项目将以0度角垂直固定在VTS窗口中。
          如果您希望项目相对于VTS窗口面向特定方向，您可以使用此选项。
        - RelativeToCurrentItemRotation: 相对于项目当前的角度。如果传入0作为角度，
          这意味着项目将以其当前的角度固定，即其当前旋转不会改变。如果您不想改变项目旋转，
          只想按原样固定它，您可以使用此选项。
        - RelativeToModel: 相对于模型旋转的角度。这意味着如果您传入0作为角度，而用户旋转了模型，
          项目将相对于模型垂直固定。这个"模型旋转"不包括Live2D ArtMesh变形引起的旋转，
          只包括VTube Studio应用于整个模型的实际旋转。如果您希望项目相对于模型的当前旋转面向特定方向，
          您可以使用此选项。
        - RelativeToPinPosition: 相对于固定位置的角度。如果您想将项目固定在某个ArtMesh内的某个位置，
          并以某个角度固定，并且希望无论模型现在如何旋转或ArtMesh如何变形，该角度都完全相同，
          那么您应该使用此选项。
          但是，为了获得所需的效果，您必须传入的角度对于每个固定位置来说都是完全不同的。
        
        sizeRelativeTo:
        - RelativeToWorld: 绝对大小。在0（最小）和1（最大）之间。
        - RelativeToCurrentItemSize: 相对于当前项目大小。您可以传入-1到1之间的数字，
          这些数字将被添加到当前项目大小中，这意味着如果您想以当前大小固定项目而不改变它，
          可以传入0。
        
        vertexPinType:
        - Provided: 项目将使用vertexID1、vertexID2、vertexID3、vertexWeight1、vertexWeight2和vertexWeight3字段
          中提供的固定位置固定到给定的ArtMesh。
        - Center: 项目将被固定到给定ArtMesh的"中心"。它实际上不是中心（空间上），而是网格三角形列表中间的三角形。
          这将为给定的ArtMesh每次提供相同的位置。
        - Random: 项目将被固定到给定ArtMesh内的随机三角形。
        
        如果提供的模型ID与加载的模型不匹配，将返回错误。您也可以将模型ID留空，
        这将尝试将其固定到当前加载的模型（如果有）。
        
        如果模型没有您提供的ArtMesh ID的ArtMesh，将返回错误。如果您将ArtMesh ID留空，
        将在模型中选择一个随机的ArtMesh。
        
        例如，您可以将模型ID和ArtMesh ID都留空，并将vertexPinType设置为Random。
        这将把项目固定在当前加载模型的随机ArtMesh上的随机位置。
        
        固定到特定位置：
        如果将vertexPinType设置为Provided，您必须使用vertexID1、vertexID2、vertexID3、
        vertexWeight1、vertexWeight2和vertexWeight3字段来提供您使用modelID和artMeshID字段选择的ArtMesh上的有效位置。
        这三个顶点ID字段必须是给定ArtMesh中三角形的顶点ID。
        要定义该特定三角形内的位置，请使用顶点权重字段。
        这些字段将与顶点位置相乘，以定义三角形中的位置。请记住，权重必须精确地加起来等于1，
        否则结果位置将在三角形之外(并将返回错误)。这些是重心坐标。
        要获取这些位置之一，您可以使用ModelClickedEvent。当模型被点击时，此事件将返回您可以与ItemPinRequest一起使用的固定位置。

        参数:
            pin: 是否固定项目
            item_instance_id: 项目实例ID
            angle_relative_to: 角度相对参考选项:
                - "RelativeToWorld": 绝对角度，相对于VTS窗口
                - "RelativeToCurrentItemRotation": 相对于项目当前的角度
                - "RelativeToModel": 相对于模型旋转的角度
                - "RelativeToPinPosition": 相对于固定位置的角度
            size_relative_to: 大小相对参考选项:
                - "RelativeToWorld": 绝对大小，0到1之间
                - "RelativeToCurrentItemSize": 相对于当前项目大小，-1到1之间
            vertex_pin_type: 顶点固定类型选项:
                - "Provided": 使用提供的顶点信息固定
                - "Center": 固定到ArtMesh的"中心"三角形
                - "Random": 固定到ArtMesh内的随机三角形
            pin_info: 固定信息对象，当pin为True时需要提供:
                - modelID: 模型ID（可选，留空则使用当前加载的模型）
                - artMeshID: ArtMesh ID（可选，留空则随机选择）
                - angle: 角度
                - size: 大小
                - vertexID1: 顶点ID1（当vertex_pin_type为"Provided"时需要）
                - vertexID2: 顶点ID2（当vertex_pin_type为"Provided"时需要）
                - vertexID3: 顶点ID3（当vertex_pin_type为"Provided"时需要）
                - vertexWeight1: 顶点权重1（当vertex_pin_type为"Provided"时需要，权重和必须为1）
                - vertexWeight2: 顶点权重2（当vertex_pin_type为"Provided"时需要，权重和必须为1）
                - vertexWeight3: 顶点权重3（当vertex_pin_type为"Provided"时需要，权重和必须为1）
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "ItemPinRequest",
                "data": {
                    "pin": true,
                    "itemInstanceID": "4a241269394f463ca16b8b21aa636568",
                    "angleRelativeTo": "RelativeToModel",
                    "sizeRelativeTo": "RelativeToWorld",
                    "vertexPinType": "Provided",
                    "pinInfo": {
                        "modelID": "d87b771d2902473bbaa0226d03ef4754",
                        "artMeshID": "hair_right_4",
                        "angle": 23.938,
                        "size": 0.33,
                        "vertexID1": 17,
                        "vertexID2": 9,
                        "vertexID3": 55,
                        "vertexWeight1": 0.25928378105163576,
                        "vertexWeight2": 0.6850675940513611,
                        "vertexWeight3": 0.055648624897003177
                    }
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "ItemPinResponse",
                "data": {
                    "isPinned": true,
                    "itemInstanceID": "4a241269394f463ca16b8b21aa636568",
                    "itemFileName": "my_test_item_2.png"
                }
            }

        返回值:
            包含项目固定状态的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "pin": pin,
            "itemInstanceID": item_instance_id,
            "angleRelativeTo": angle_relative_to,
            "sizeRelativeTo": size_relative_to,
            "vertexPinType": vertex_pin_type
        }
        
        # 只有当pin为True时，才需要提供pin_info
        if pin and pin_info is not None:
            data["pinInfo"] = pin_info
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="ItemPinRequest",
            data=data
        )
    async def post_processing_list_request(self, fill_post_processing_presets_array=True,
                                         fill_post_processing_effects_array=True,
                                         effect_id_filter=None,
                                         request_id=None):
        """获取后处理效果和状态列表

        该请求返回后处理系统的常规状态、所有现有（用户创建的）后处理预设列表
        以及所有可用后处理效果及其当前值的列表（当前后处理状态）。

        如果将fillPostProcessingPresetsArray设置为false，响应负载中的postProcessingPresets数组将为空。
        请求后处理预设列表需要从磁盘读取预设文件（尽管它们在VTS中缓存了一段时间），这可能会很慢。
        如果您以较高频率发送此请求，请确保fillPostProcessingPresetsArray不是true，
        否则可能会由于磁盘I/O而造成延迟。

        如果将fillPostProcessingEffectsArray设置为false，响应负载中的postProcessingEffects数组将为空。
        如果您不需要后处理效果及其值的完整列表，建议将fillPostProcessingEffectsArray设置为false，
        因为响应负载可能会相当大（无筛选时可能有数千行）。

        如果您只对特定的后处理效果感兴趣，可以在effectIDFilter数组中列出它们。否则，将数组留空以不应用筛选器。

        效果何时被视为"活动"？
        每个效果至少有一个float配置（但可以有多个）将activationConfig设置为true。
        如果一个效果的其中一个配置的值大于0，则该效果被视为活动。
        例如，对于ColorGrading效果，这将是ColorGrading_Strength配置。

        参数:
            fill_post_processing_presets_array: 是否填充后处理预设数组
            fill_post_processing_effects_array: 是否填充后处理效果数组
            effect_id_filter: 效果ID筛选器数组，如果为None则返回所有效果
            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "PostProcessingListRequest",
                "data": {
                    "fillPostProcessingPresetsArray": true,
                    "fillPostProcessingEffectsArray": true,
                    "effectIDFilter": ["ASCII", "ColorGrading", "WeatherEffects", "ChromaticAberration"]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "PostProcessingListResponse",
                "data": {
                    "postProcessingSupported": true,
                    "postProcessingActive": true,
                    "canSendPostProcessingUpdateRequestRightNow": true,
                    "restrictedEffectsAllowed": false,
                    "presetIsActive": true,
                    "activePreset": "some_effects_preset_3",
                    "presetCount": 70,
                    "activeEffectCount": 5,
                    "effectCountBeforeFilter": 29,
                    "configCountBeforeFilter": 258,
                    "effectCountAfterFilter": 4,
                    "configCountAfterFilter": 31,
                    "postProcessingEffects": [
                        {
                            "internalID": "color_grading",
                            "enumID": "ColorGrading",
                            "explanation": "Color grading",
                            "effectIsActive": false,
                            "effectIsRestricted": false,
                            "configEntries": [
                                {
                                    "internalID": "color_grading-strength",
                                    "enumID": "ColorGrading_Strength",
                                    "explanation": "Effect on/off",
                                    "type": "Float",
                                    "activationConfig": true,
                                    "floatValue": 0.0,
                                    "floatMin": 0.0,
                                    "floatMax": 1.0,
                                    "floatDefault": 0.0,
                                    "intValue": 0,
                                    "intMin": 0,
                                    "intMax": 0,
                                    "intDefault": 0,
                                    "colorValue": "",
                                    "colorDefault": "",
                                    "colorHasAlpha": false,
                                    "boolValue": false,
                                    "boolDefault": false,
                                    "stringValue": "",
                                    "stringDefault": "",
                                    "sceneItemValue": "",
                                    "sceneItemDefault": ""
                                },
                                {
                                    "internalID": "color_grading-color_filter",
                                    "enumID": "ColorGrading_ColorFilter",
                                    "explanation": "Color filter",
                                    "type": "color",
                                    "activationConfig": false,
                                    "floatValue": 0.0,
                                    "floatMin": 0.0,
                                    "floatMax": 0.0,
                                    "floatDefault": 0.0,
                                    "intValue": 0,
                                    "intMin": 0,
                                    "intMax": 0,
                                    "intDefault": 0,
                                    "colorValue": "FFFFFFFF",
                                    "colorDefault": "FFFFFFFF",
                                    "colorHasAlpha": false,
                                    "boolValue": false,
                                    "boolDefault": false,
                                    "stringValue": "",
                                    "stringDefault": "",
                                    "sceneItemValue": "",
                                    "sceneItemDefault": ""
                                }
                            ]
                        }
                    ],
                    "postProcessingPresets": [
                        "My Cool Preset",
                        "some_effects_preset_1",
                        "some_effects_preset_2",
                        "some_effects_preset_3",
                        "test asdf 123456",
                        "blur and color grading"
                    ]
                }
            }

        返回值:
            包含后处理系统状态、预设列表和效果列表的响应数据
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "fillPostProcessingPresetsArray": fill_post_processing_presets_array,
            "fillPostProcessingEffectsArray": fill_post_processing_effects_array,
            "effectIDFilter": effect_id_filter if effect_id_filter is not None else []
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="PostProcessingListRequest",
            data=data
        )
    async def post_processing_update_request(self, post_processing_on=True,
                                        set_post_processing_preset=False,
                                        set_post_processing_values=False,
                                        preset_to_set="",
                                        post_processing_fade_time=0.0,
                                        set_all_other_values_to_default=False,
                                        using_restricted_effects=False,
                                        randomize_all=False,
                                        randomize_all_chaos_level=0.0,
                                        post_processing_values=None,
                                        request_id=None):
        """设置后处理效果

        VTube Studio内置了后处理功能，允许您直接向场景添加视觉效果。
        该功能在Windows和macOS上可用。术语"视觉效果"、"VFX"和"后处理效果"在本文档中可互换使用。

        使用PostProcessingUpdateRequest，您可以控制后处理系统。
        您可以打开/关闭它，加载/卸载预设，甚至直接详细控制单个配置项（颜色、强度等）。

        重要限制:
            - 只有在没有打开与后处理配置相关的窗口时，才能发送此更新。
            否则，将返回错误PostProcessingUpdateReqestCannotUpdateRightNow。
            - 不能同时设置预设和单个配置值，如果两者都设置为true，将返回错误PostProcessingUpdateRequestLoadingPresetAndValues。
            - 淡入淡出时间必须在0到2秒之间，否则将返回错误PostProcessingUpdateRequestFadeTimeInvalid。

        参数:
            post_processing_on: 是否全局开启后处理效果
                - 与VTS UI上的后处理开关功能相同
                - 即使设置为false，仍然可以设置预设或单个配置值
                - 设置的值会被保存，但不会在屏幕上显示

            set_post_processing_preset: 是否设置后处理预设
                - 设置为true时，必须提供preset_to_set参数
                - 如果预设不存在，将返回错误PostProcessingUpdateRequestPresetFileLoadFailed

            set_post_processing_values: 是否设置后处理值
                - 设置为true时，必须通过post_processing_values数组提供配置项

            preset_to_set: 要设置的预设名称
                - 仅需提供预设名称，无需文件扩展名

            post_processing_fade_time: 后处理淡入淡出时间（秒）
                - 新值将从旧值平滑过渡到新值
                - 必须在0到2秒之间

            set_all_other_values_to_default: 是否将所有其他值设置为默认值
                - true: 未在请求中提及的所有值将淡回默认值（即关闭所有未提及的效果）
                - false: 未在请求中提及的所有值将保持不变

            using_restricted_effects: 是否使用受限/实验性效果
                - 设置为true时，允许使用受限效果
                - 用户必须在VTube Studio的VFX设置中手动启用了这些效果
                - 否则将返回错误PostProcessingUpdateRequestTriedToLoadRestrictedEffect

            randomize_all: 是否随机化所有效果
                - 设置为true时，将忽略发送的所有配置值或预设
                - 会随机化所有效果配置，使后处理效果随机化

            randomize_all_chaos_level: 随机化混沌级别（0.0-1.0）
                - 0.0: 仅激活1-2个效果，值较温和
                - 1.0: 激活所有效果，值较极端，可能导致画面混乱
                - 推荐值范围：0.4-0.5

            post_processing_values: 后处理值数组，每个元素包含configID和configValue
                - configID: 配置项的ID（不区分大小写，忽略_或-）
                - configValue: 要设置的值（字符串形式）
                    - 浮点数或整数：超出配置的min/max范围的值将被限制
                    - 布尔值：不区分大小写，"True"、"true"、"TRUE"等均可

            request_id: 请求ID，如果为None则自动生成

        请求示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "SomeID",
                "messageType": "PostProcessingUpdateRequest",
                "data": {
                    "postProcessingOn": true,
                    "setPostProcessingPreset": false,
                    "setPostProcessingValues": true,
                    "presetToSet": "",
                    "postProcessingFadeTime": 1.3,
                    "setAllOtherValuesToDefault": true,
                    "usingRestrictedEffects": false,
                    "randomizeAll": false,
                    "randomizeAllChaosLevel": 0.0,
                    "postProcessingValues": [
                        {
                            "configID": "Backlight_Strength",
                            "configValue": "0.8"
                        },
                        {
                            "configID": "Bloom_Strength",
                            "configValue": "1.0"
                        },
                        {
                            "configID": "Bloom_StreakVertical",
                            "configValue": "false"
                        },
                        {
                            "configID": "Bloom_StreakColorTint",
                            "configValue": "220308FF"
                        }
                    ]
                }
            }

        响应示例:
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "timestamp": 1625405710728,
                "requestID": "SomeID",
                "messageType": "PostProcessingUpdateResponse",
                "data": {
                    "postProcessingActive": true,
                    "presetIsActive": false,
                    "activePreset": "",
                    "activeEffectCount": 2
                }
            }

        返回值:
            包含后处理系统状态的响应数据

        可能的错误:
            - PostProcessingUpdateReqestCannotUpdateRightNow: 有后处理配置相关窗口打开
            - PostProcessingUpdateRequestLoadingPresetAndValues: 同时设置了预设和单个配置值
            - PostProcessingUpdateRequestFadeTimeInvalid: 淡入淡出时间超出0-2秒范围
            - PostProcessingUpdateRequestPresetFileLoadFailed: 指定的预设不存在
            - PostProcessingUpdateRequestTriedToLoadRestrictedEffect: 尝试使用受限效果但未正确设置参数
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            
        # 构建请求数据
        data = {
            "postProcessingOn": post_processing_on,
            "setPostProcessingPreset": set_post_processing_preset,
            "setPostProcessingValues": set_post_processing_values,
            "presetToSet": preset_to_set,
            "postProcessingFadeTime": post_processing_fade_time,
            "setAllOtherValuesToDefault": set_all_other_values_to_default,
            "usingRestrictedEffects": using_restricted_effects,
            "randomizeAll": randomize_all,
            "randomizeAllChaosLevel": randomize_all_chaos_level,
            "postProcessingValues": post_processing_values if post_processing_values is not None else []
        }
        
        if self.auth_token:
            data["authenticationToken"] = self.auth_token
            
        return await self.send_request(
            api_name="VTubeStudioPublicAPI",
            message_type="PostProcessingUpdateRequest",
            data=data
        )
async def main():
    """示例用法"""
    print("===== VTube Studio API客户端示例 =====")
    
    # 使用UDP发现VTube Studio实例
    print("\n1. 正在使用UDP发现VTube Studio实例...")
    discovered_servers = await VTubeStudioAPI.discover_servers(timeout=3.0)
    
    if discovered_servers:
        print(f"\n已发现 {len(discovered_servers)} 个VTube Studio实例:")
        for i, server in enumerate(discovered_servers, 1):
            print(f"  {i}. 窗口标题: {server.get('windowTitle')}, API激活: {server.get('active')}, 端口: {server.get('port')}")
            print(f"     实例ID: {server.get('instanceID')}")
        
        # 使用第一个发现的服务器
        selected_server = discovered_servers[0]
        print(f"\n将使用第一个发现的服务器: {selected_server.get('windowTitle')}")
        api = VTubeStudioAPI(host="localhost", port=selected_server.get('port'))
    else:
        print("\n未发现VTube Studio实例，将使用默认配置")
        api = VTubeStudioAPI(host="localhost", port=8001)
    
    # 连接到服务器
    print("\n2. 正在连接到VTube Studio服务器...")
    if await api.connect():
        print("\n3. 获取API状态信息...")
        state_response = await api.get_api_state(request_id="MyTestRequestID123")
        print(json.dumps(state_response, indent=2, ensure_ascii=False))
        
        print("\n4. 获取VTS统计信息...")
        stats_response = await api.get_statistics()
        print(json.dumps(stats_response, indent=2, ensure_ascii=False))
        
        print("\n5. 获取VTS文件夹信息...")
        folders_response = await api.get_vts_folders()
        print(json.dumps(folders_response, indent=2, ensure_ascii=False))
        
        # 示例：认证流程
        print("\n6. 演示认证流程（可选）...")
        # 注意：取消下面的注释将触发VTS中的认证弹窗
        #
        # # 请求认证令牌
        # if await api.request_auth_token(
        #     plugin_name="My Cool Plugin",
        #     plugin_developer="My Name"
        # ):
        #     # 使用获取的令牌进行会话认证
        #     if await api.authenticate():
        #         print("\n7. 认证后获取可用模型...")
        #         models_response = await api.get_available_models()
        #         print(json.dumps(models_response, indent=2, ensure_ascii=False))
        # 
        # 示例：直接使用已保存的令牌
        # api.set_auth_token("your_saved_token_here", "Your Plugin Name", "Your Developer Name")
        # if await api.authenticate():
        #     print("\n已使用保存的令牌成功认证")
        
        # 断开连接
        await api.disconnect()
        print("\n8. 已断开与服务器的连接")
    else:
        print("\n连接失败，请确保VTube Studio已运行且API已启用")
        
    print("\n===== 示例结束 =====\n")

if __name__ == "__main__":
    asyncio.run(main())

