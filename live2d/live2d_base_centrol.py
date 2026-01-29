import asyncio
import random
import sys
import os
import noise
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from urllib.parse import parse_qs, urlparse

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtuber_studio_info import VTubeStudioAPI
current_angles = {"x": 0.0, "y": 0.0, "z": 0.0} 
velocities = {"x": 0.0, "y": 0.0, "z": 0.0}  
t = 0  
direction_x = 0 
direction_y = 0 
direction_z = 0 

# 全局控制器实例，用于在HTTP处理器中访问
global_controller = None

class RequestHandler(BaseHTTPRequestHandler):
    """处理HTTP请求的处理器"""
    
    def do_POST(self):
        """处理POST请求"""
        global global_controller
        
        # 获取请求内容长度
        content_length = int(self.headers['Content-Length'])
        # 读取请求体
        post_data = self.rfile.read(content_length)
        
        try:
            # 解析JSON请求
            request_data = json.loads(post_data.decode('utf-8'))
            
            # 提取请求参数
            function = request_data.get('function', '')
            duration = request_data.get('time', 0)
            params = request_data.get('params', {})
            
            print(f"收到请求: 功能={function}, 时间={duration}, 参数={params}")
            
            # 根据功能切换状态
            if function == "回答问题":
                asyncio.run(global_controller.set_state("回答问题"))
                # 如果指定了时间，设置定时器在时间结束后返回无事件状态
                if duration > 0:
                    threading.Timer(duration, lambda: asyncio.run(global_controller.set_state("无事件"))).start()
            elif function == "有事件":
                asyncio.run(global_controller.set_state("有事件"))
                if duration > 0:
                    threading.Timer(duration, lambda: asyncio.run(global_controller.set_state("无事件"))).start()
            elif function == "无事件":
                asyncio.run(global_controller.set_state("无事件"))
            else:
                print(f"未知功能: {function}")
            
            # 返回成功响应
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'status': 'success', 'message': f'状态已更新为: {global_controller.current_state}'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            
        except json.JSONDecodeError:
            # JSON解析错误
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'status': 'error', 'message': '无效的JSON格式'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        except Exception as e:
            # 其他错误
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'status': 'error', 'message': str(e)}
            self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def log_message(self, format, *args):
        """重写日志方法，关闭默认日志输出"""
        return

class Live2DBaseControl:
    """Live2D模型基础控制器"""
    
    def __init__(self, host="localhost", port=8001, http_port=8888):
        """初始化控制器
        
        参数:
            host (str): VTube Studio服务器主机名
            port (int): VTube Studio服务器端口
            http_port (int): HTTP服务器监听端口
        """
        self.api = VTubeStudioAPI(host=host, port=port)
        self.current_state = "无事件"  # 默认状态：无事件
        self.running = False
        self.taking_over = False  # 标记是否完全接管控制
        self.http_port = http_port
        self.http_server = None
        self.http_thread = None
    
    async def connect(self):
        """连接到VTube Studio服务器"""
        return await self.api.connect()
    
    async def login(self, plugin_name="Live2DBaseControl", plugin_developer="Developer"):
        """登录到VTube Studio服务器
        
        参数:
            plugin_name (str): 插件名称
            plugin_developer (str): 开发者名称
        """
        # 请求认证令牌
        if not await self.api.request_auth_token(plugin_name, plugin_developer):
            print("请求认证令牌失败")
            return False
        
        # 进行认证
        if not await self.api.authenticate(plugin_name, plugin_developer):
            print("认证失败")
            return False
        
        print("登录成功")
        return True
    
    async def disconnect(self):
        """断开与VTube Studio服务器的连接"""
        await self.api.disconnect()
    
    async def take_over_control(self, take_over=True):
        """接管或释放Live2D模型的控制
        
        参数:
            take_over (bool): True表示接管控制，False表示释放控制
        """
        self.taking_over = take_over
        if take_over:
            print("已接管Live2D模型控制，所有面部参数将由程序控制")
        else:
            print("已释放Live2D模型控制，模型将恢复使用真实世界面部数据")
        return True
    
    async def set_state(self, state):
        """设置当前状态
        
        参数:
            state (str): 状态名称（"无事件"、"有事件"或"回答问题"）
        """
        if state not in ["无事件", "有事件", "回答问题"]:
            print(f"无效状态: {state}")
            return False
        
        self.current_state = state
        print(f"状态已变更为: {self.current_state}")
        return True
    
    def start_http_server(self):
        """启动HTTP服务器"""
        global global_controller
        global_controller = self
        
        # 创建HTTP服务器
        server_address = ('', self.http_port)
        self.http_server = HTTPServer(server_address, RequestHandler)
        print(f"HTTP服务器已启动，监听端口 {self.http_port}")
        
        # 在单独的线程中运行HTTP服务器
        self.http_thread = threading.Thread(target=self.http_server.serve_forever)
        self.http_thread.daemon = True
        self.http_thread.start()
    
    def stop_http_server(self):
        """停止HTTP服务器"""
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
            print("HTTP服务器已停止")

    async def idle(self):
        """执行小幅度随机面部表情移动"""
        # 根据参数表定义面部参数列表
        face_parameters = [
            "FacePositionX", "FacePositionY", "FacePositionZ", "FaceAngleX", "FaceAngleY", "FaceAngleZ",
            "MouthSmile", "MouthOpen", "Brows", "TongueOut", "CheekPuff", "FaceAngry", "BrowLeftY", 
            "BrowRightY", "EyeOpenLeft", "EyeOpenRight", "EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY",
            "MousePositionX", "MousePositionY", "VoiceVolume", "VoiceFrequency", "VoiceVolumePlusMouthOpen",
            "VoiceFrequencyPlusMouthSmile", "VoiceA", "VoiceI", "VoiceU", "VoiceE", "VoiceO", "VoiceSilence",
            "MouthX", "HandLeftFound", "HandRightFound", "BothHandsFound", "HandDistance", "HandLeftPositionX",
            "HandLeftPositionY", "HandLeftPositionZ", "HandRightPositionX", "HandRightPositionY", "HandRightPositionZ",
            "HandLeftAngleX", "HandLeftAngleZ", "HandRightAngleX", "HandRightAngleZ", "HandLeftOpen", "HandRightOpen",
            "HandLeftFinger_1_Thumb", "HandLeftFinger_2_Index", "HandLeftFinger_3_Middle", "HandLeftFinger_4_Ring",
            "HandLeftFinger_5_Pinky", "HandRightFinger_1_Thumb", "HandRightFinger_2_Index", "HandRightFinger_3_Middle",
            "HandRightFinger_4_Ring", "HandRightFinger_5_Pinky", "MocopiConnected", "MocopiHipAngleZ", "MocopiAngleX",
            "MocopiAngleY", "MocopiAngleZ", "MocopiBodyAngleX", "MocopiBodyAngleY", "MocopiBodyAngleZ", "MocopiBodyPositionX",
            "MocopiBodyPositionY", "MocopiBodyPositionZ", "MocopiUpperArmLeftAngleY", "MocopiUpperArmLeftAngleZ",
            "MocopiUpperArmRightAngleY", "MocopiUpperArmRightAngleZ", "MocopiLowerArmLeftAngleX", "MocopiLowerArmLeftAngleY",
            "MocopiLowerArmLeftAngleZ", "MocopiLowerArmRightAngleX", "MocopiLowerArmRightAngleY", "MocopiLowerArmRightAngleZ",
            "MocopiUpperLegLeftAngleY", "MocopiUpperLegLeftAngleZ", "MocopiUpperLegRightAngleY", "MocopiUpperLegRightAngleZ",
            "MocopiLowerLegLeftAngleY", "MocopiLowerLegLeftAngleZ", "MocopiLowerLegRightAngleY", "MocopiLowerLegRightAngleZ"
        ]
        
        try:
            # 获取当前模型的参数信息，以了解参数的范围
            param_list_response = await self.api.get_tracking_parameters()
            if param_list_response and "data" in param_list_response and "defaultParameters" in param_list_response["data"]:
                available_params = {param["name"]: param for param in param_list_response["data"]["defaultParameters"]}
                
                # 只保留模型中实际存在的参数
                valid_params = [param for param in face_parameters if param in available_params]
            else:
                # 如果无法获取参数列表，使用默认参数
                valid_params = face_parameters
                available_params = {}
        except Exception as e:
            print(f"获取参数列表失败: {e}")
            valid_params = face_parameters
            available_params = {}
        
        while self.running and self.current_state == "无事件":
            try:
                # 获取当前参数值
                current_params_response = await self.api.get_tracking_parameters()
                if current_params_response and "data" in current_params_response and "defaultParameters" in current_params_response["data"]:
                    current_params = {param["name"]: param for param in current_params_response["data"]["defaultParameters"]}
                else:
                    current_params = {}
                
                # 为每个有效参数生成基于其范围的微调值
                parameter_values = []
                global current_angles, velocities, t, direction_x, direction_y, direction_z
                t += 0.02  # 让运动更平滑

                # 让方向有一定概率改变，避免持续朝同一方向
                if random.random() < 0.5:  
                    direction_x *= -1  
                if random.random() < 0.5:
                    direction_y *= -1  
                if random.random() < 0.5:
                    direction_z *= -1  

                # 计算目标角度，并限制最大幅度
                target_x = max(min(direction_x * noise.pnoise1(t, repeat=1000) * 10 + random.uniform(-3, 3), 15), -15)
                target_y = max(min(direction_y * noise.pnoise1(t + 100, repeat=1000) * 20 + random.uniform(-5, 5), 20), -20)
                target_z = max(min(direction_z * noise.pnoise1(t + 200, repeat=1000) * 25 + random.uniform(-8, 8), 25), -25)

                # 细微的抖动
                micro_noise = lambda: noise.pnoise1(t * 8, repeat=1000) * 1.2
                target_x += micro_noise()
                target_y += micro_noise()
                target_z += micro_noise()

                # 调整惯性，让运动更快调整方向
                inertia = 0.75  
                velocities["x"] = velocities["x"] * inertia + (target_x - current_angles["x"]) * (1 - inertia)
                velocities["y"] = velocities["y"] * inertia + (target_y - current_angles["y"]) * (1 - inertia)
                velocities["z"] = velocities["z"] * inertia + (target_z - current_angles["z"]) * (1 - inertia)

                # 更新角度
                current_angles["x"] += velocities["x"]
                current_angles["y"] += velocities["y"]
                current_angles["z"] += velocities["z"]
                parameter_values =[
                    {"id": "FaceAngleX", "value": current_angles["x"]},
                    {"id": "FaceAngleY", "value": current_angles["y"]},
                    {"id": "FaceAngleZ", "value": current_angles["z"]}
                ]
                
                # 发送参数数据，设置faceFound=True以表明我们在控制面部
                # 设置mode="set"以确保我们的参数值覆盖其他来源
                #print(f"当前参数值: {parameter_values}")
                await self.api.inject_parameter_data(parameter_values, face_found=True, mode="set")
                
                # 等待一小段时间（0.1秒），然后再次更新
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"更新面部参数失败: {e}")
                await asyncio.sleep(1)
    
    async def handle_event_state(self):
        """处理有事件状态（留空，等待后续实现）"""
        while self.running and self.current_state == "有事件":
            # 有事件状态的代码将在这里实现
            # 在有事件状态下，我们也应该持续发送参数以保持控制
            try:
                # 发送基础参数以保持控制
                parameter_values = [
                    {"id": "ParamAngleX", "value": 0},
                    {"id": "ParamAngleY", "value": 0},
                    {"id": "ParamAngleZ", "value": 0},
                    {"id": "ParamEyeLOpen", "value": 1.0},
                    {"id": "ParamEyeROpen", "value": 1.0},
                    {"id": "ParamMouthOpenY", "value": 0.0}
                ]
                
                await self.api.inject_parameter_data(parameter_values, face_found=True, mode="set")
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"事件状态下更新参数失败: {e}")
                await asyncio.sleep(1)
    
    async def ask_state(self):
        """处理回答问题状态，实现更明显的面部移动"""
        # 使用与idle相同的面部移动逻辑，但调整参数使其更明显
        while self.running and self.current_state == "回答问题":
            try:
                # 简单的面部移动
                global current_angles, velocities, t, direction_x, direction_y, direction_z
                t += 0.02
                
                # 方向控制（比无事件状态下更大的变化）
                if random.random() < 0.1:  # 降低方向改变的频率，让动作更流畅
                    direction_x *= -1
                if random.random() < 0.1:
                    direction_y *= -1
                if random.random() < 0.1:
                    direction_z *= -1
                
                # 更大幅度的面部角度变化
                target_x = max(min(direction_x * noise.pnoise1(t, repeat=1000) * 20 + random.uniform(-5, 5), 30), -30)
                target_y = max(min(direction_y * noise.pnoise1(t + 100, repeat=1000) * 25 + random.uniform(-8, 8), 35), -35)
                target_z = max(min(direction_z * noise.pnoise1(t + 200, repeat=1000) * 30 + random.uniform(-10, 10), 40), -40)
                
                # 添加细微抖动
                micro_noise = lambda: noise.pnoise1(t * 8, repeat=1000) * 1.5
                target_x += micro_noise()
                target_y += micro_noise()
                target_z += micro_noise()
                
                # 调整惯性
                inertia = 0.7
                velocities["x"] = velocities["x"] * inertia + (target_x - current_angles["x"]) * (1 - inertia)
                velocities["y"] = velocities["y"] * inertia + (target_y - current_angles["y"]) * (1 - inertia)
                velocities["z"] = velocities["z"] * inertia + (target_z - current_angles["z"]) * (1 - inertia)
                
                # 更新角度
                current_angles["x"] += velocities["x"]
                current_angles["y"] += velocities["y"]
                current_angles["z"] += velocities["z"]
                
                # 口型配合 - 随机变化的嘴巴张开程度
                mouth_open = 0.3 + random.uniform(0.2, 0.5) * noise.pnoise1(t * 5, repeat=1000)
                mouth_open = max(0.1, min(0.9, mouth_open))  # 限制在0.1到0.9之间
                
                # 构建参数列表（注意使用正确的参数名）
                parameter_values = [
                    {"id": "FaceAngleX", "value": current_angles["x"]},
                    {"id": "FaceAngleY", "value": current_angles["y"]},
                    {"id": "FaceAngleZ", "value": current_angles["z"]},
                    {"id": "EyeOpenLeft", "value": 1.0},
                    {"id": "EyeOpenRight", "value": 1.0},
                    {"id": "MouthOpen", "value": mouth_open}
                ]
                
                await self.api.inject_parameter_data(parameter_values, face_found=True, mode="set")
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"回答问题状态下更新参数失败: {e}")
                await asyncio.sleep(1)
    
    async def run(self):
        """启动控制器，开始状态管理"""
        self.running = True
        print("Live2D控制器已启动")
        
        # 启动HTTP服务器
        self.start_http_server()
        
        # 默认接管控制
        await self.take_over_control(True)
        
        try:
            while self.running:
                print(f"当前状态: {self.current_state}")
                if self.current_state == "无事件":
                    await self.idle()
                elif self.current_state == "有事件":
                    await self.handle_event_state()
                elif self.current_state == "回答问题":
                    await self.ask_state()
                
                # 短暂休眠，避免CPU占用过高
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"控制器运行出错: {e}")
        finally:
            self.running = False
            # 停止HTTP服务器
            self.stop_http_server()
    
    async def stop(self):
        """停止控制器"""
        self.running = False
        print("Live2D控制器已停止")


# 使用示例
async def main():
    # 创建控制器实例，监听8888端口
    controller = Live2DBaseControl(http_port=8888)
    
    try:
        # 连接服务器
        if not await controller.connect():
            return
        
        # 登录认证
        if not await controller.login():
            await controller.disconnect()
            return
        
        # 启动控制器
        await controller.run()
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        # 断开连接
        await controller.disconnect()


if __name__ == "__main__":
    asyncio.run(main())