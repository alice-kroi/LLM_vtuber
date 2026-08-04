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
import math

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtuber_studio_info import VTubeStudioAPI
current_angles = {"x": 0.0, "y": 0.0, "z": 0.0} 
velocities = {"x": 0.0, "y": 0.0, "z": 0.0}  
t = 0  
direction_x = 0 
direction_y = 0 
direction_z = 0 

# 鼠标位置追踪
global_mouse_pos = {"x": 0.5, "y": 0.5}  # 归一化坐标 (0-1)
mouse_filtered = {"x": 0.5, "y": 0.5}  # 滤波后的鼠标位置

# 平滑参数
SMOOTHING_FACTOR = 0.8  # 低通滤波系数
MOUSE_SENSITIVITY = 100.0  # 鼠标灵敏度（增加以提高动作幅度）

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
            elif function == "update_mouse":
                # 更新鼠标位置
                mouse_x = params.get("x", 0.5)
                mouse_y = params.get("y", 0.5)
                global global_mouse_pos
                global_mouse_pos = {"x": float(mouse_x), "y": float(mouse_y)}
                print(f"更新鼠标位置: ({mouse_x}, {mouse_y})")
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
                global global_mouse_pos, mouse_filtered, SMOOTHING_FACTOR, MOUSE_SENSITIVITY
                t += 0.02  # 让运动更平滑

                # 鼠标位置低通滤波
                mouse_filtered["x"] = mouse_filtered["x"] * SMOOTHING_FACTOR + global_mouse_pos["x"] * (1 - SMOOTHING_FACTOR)
                mouse_filtered["y"] = mouse_filtered["y"] * SMOOTHING_FACTOR + global_mouse_pos["y"] * (1 - SMOOTHING_FACTOR)

                # 根据鼠标位置计算目标角度
                # 鼠标坐标归一化到 (-1, 1)
                mouse_norm_x = (mouse_filtered["x"] - 0.5) * 2.0
                mouse_norm_y = (mouse_filtered["y"] - 0.5) * 2.0

                # 计算基础目标角度（基于鼠标位置）
                base_target_x = mouse_norm_y * MOUSE_SENSITIVITY * 1.5  # 垂直鼠标移动影响X角度（低头/抬头）
                base_target_y = mouse_norm_x * MOUSE_SENSITIVITY * 1.5  # 水平鼠标移动影响Y角度（左右转头）
                base_target_z = mouse_norm_x * MOUSE_SENSITIVITY * 1.0  # 水平鼠标移动影响Z角度（左右倾斜）

                # 添加随机微动，增加自然感（增加幅度以提高动作幅度）
                micro_noise = lambda: noise.pnoise1(t * 8, repeat=1000) * 3.0
                target_x = base_target_x + micro_noise()
                target_y = base_target_y + micro_noise()
                target_z = base_target_z + micro_noise()

                # 限制最大幅度（增加范围以提高动作幅度）
                target_x = max(min(target_x, 25), -25)
                target_y = max(min(target_y, 30), -30)
                target_z = max(min(target_z, 35), -35)

                # 使用 PID 控制算法优化运动平滑度
                Kp = 0.3  # 比例系数
                Ki = 0.01  # 积分系数
                Kd = 0.2  # 微分系数

                # 计算误差
                error_x = target_x - current_angles["x"]
                error_y = target_y - current_angles["y"]
                error_z = target_z - current_angles["z"]

                # 计算导数（速度）
                derivative_x = error_x - velocities["x"]
                derivative_y = error_y - velocities["y"]
                derivative_z = error_z - velocities["z"]

                # 更新速度
                velocities["x"] = Kp * error_x + Kd * derivative_x
                velocities["y"] = Kp * error_y + Kd * derivative_y
                velocities["z"] = Kp * error_z + Kd * derivative_z

                # 平滑速度变化
                velocities["x"] = velocities["x"] * 0.8 + velocities["x"] * 0.2
                velocities["y"] = velocities["y"] * 0.8 + velocities["y"] * 0.2
                velocities["z"] = velocities["z"] * 0.8 + velocities["z"] * 0.2

                # 更新角度
                current_angles["x"] += velocities["x"]
                current_angles["y"] += velocities["y"]
                current_angles["z"] += velocities["z"]

                # 限制最终角度范围（增加范围以提高动作幅度）
                current_angles["x"] = max(min(current_angles["x"], 25), -25)
                current_angles["y"] = max(min(current_angles["y"], 30), -30)
                current_angles["z"] = max(min(current_angles["z"], 35), -35)

                # 构建参数值
                parameter_values =[
                    {"id": "FaceAngleX", "value": current_angles["x"]},
                    {"id": "FaceAngleY", "value": current_angles["y"]},
                    {"id": "FaceAngleZ", "value": current_angles["z"]},
                    {"id": "EyeLeftX", "value": mouse_norm_x * 0.8},  # 眼球跟随鼠标（增加系数以提高动作幅度）
                    {"id": "EyeRightX", "value": mouse_norm_x * 0.8},
                    {"id": "EyeLeftY", "value": -mouse_norm_y * 0.8},
                    {"id": "EyeRightY", "value": -mouse_norm_y * 0.8}
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
        """处理有事件状态，支持鼠标位置响应"""
        while self.running and self.current_state == "有事件":
            try:
                global global_mouse_pos, mouse_filtered, SMOOTHING_FACTOR, MOUSE_SENSITIVITY
                
                # 鼠标位置低通滤波
                mouse_filtered["x"] = mouse_filtered["x"] * SMOOTHING_FACTOR + global_mouse_pos["x"] * (1 - SMOOTHING_FACTOR)
                mouse_filtered["y"] = mouse_filtered["y"] * SMOOTHING_FACTOR + global_mouse_pos["y"] * (1 - SMOOTHING_FACTOR)

                # 根据鼠标位置计算目标角度
                mouse_norm_x = (mouse_filtered["x"] - 0.5) * 2.0
                mouse_norm_y = (mouse_filtered["y"] - 0.5) * 2.0

                # 计算目标角度（有事件状态下更明显的响应）
                target_x = mouse_norm_y * MOUSE_SENSITIVITY * 1.2
                target_y = mouse_norm_x * MOUSE_SENSITIVITY * 1.2
                target_z = mouse_norm_x * MOUSE_SENSITIVITY * 0.6

                # 限制最大幅度（增加范围以提高动作幅度）
                target_x = max(min(target_x, 25), -25)
                target_y = max(min(target_y, 30), -30)
                target_z = max(min(target_z, 35), -35)

                # 平滑过渡到目标角度
                global current_angles, velocities
                inertia = 0.7
                velocities["x"] = velocities["x"] * inertia + (target_x - current_angles["x"]) * (1 - inertia)
                velocities["y"] = velocities["y"] * inertia + (target_y - current_angles["y"]) * (1 - inertia)
                velocities["z"] = velocities["z"] * inertia + (target_z - current_angles["z"]) * (1 - inertia)

                # 更新角度
                current_angles["x"] += velocities["x"]
                current_angles["y"] += velocities["y"]
                current_angles["z"] += velocities["z"]

                # 限制最终角度范围（增加范围以提高动作幅度）
                current_angles["x"] = max(min(current_angles["x"], 25), -25)
                current_angles["y"] = max(min(current_angles["y"], 30), -30)
                current_angles["z"] = max(min(current_angles["z"], 35), -35)

                # 构建参数值
                parameter_values = [
                    {"id": "FaceAngleX", "value": current_angles["x"]},
                    {"id": "FaceAngleY", "value": current_angles["y"]},
                    {"id": "FaceAngleZ", "value": current_angles["z"]},
                    {"id": "EyeOpenLeft", "value": 1.0},
                    {"id": "EyeOpenRight", "value": 1.0},
                    {"id": "EyeLeftX", "value": mouse_norm_x * 0.8},  # 眼球跟随鼠标（增加系数以提高动作幅度）
                    {"id": "EyeRightX", "value": mouse_norm_x * 0.8},
                    {"id": "EyeLeftY", "value": -mouse_norm_y * 0.8},
                    {"id": "EyeRightY", "value": -mouse_norm_y * 0.8},
                    {"id": "MouthOpen", "value": 0.3}  # 轻微张开嘴巴（增加幅度）
                ]
                
                await self.api.inject_parameter_data(parameter_values, face_found=True, mode="set")
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"事件状态下更新参数失败: {e}")
                await asyncio.sleep(1)
    
    async def ask_state(self):
        """处理回答问题状态，支持鼠标位置响应和更明显的面部移动"""
        while self.running and self.current_state == "回答问题":
            try:
                global current_angles, velocities, t, direction_x, direction_y, direction_z
                global global_mouse_pos, mouse_filtered, SMOOTHING_FACTOR, MOUSE_SENSITIVITY
                t += 0.02
                
                # 鼠标位置低通滤波
                mouse_filtered["x"] = mouse_filtered["x"] * SMOOTHING_FACTOR + global_mouse_pos["x"] * (1 - SMOOTHING_FACTOR)
                mouse_filtered["y"] = mouse_filtered["y"] * SMOOTHING_FACTOR + global_mouse_pos["y"] * (1 - SMOOTHING_FACTOR)

                # 根据鼠标位置计算目标角度
                mouse_norm_x = (mouse_filtered["x"] - 0.5) * 2.0
                mouse_norm_y = (mouse_filtered["y"] - 0.5) * 2.0

                # 计算基础目标角度（基于鼠标位置）
                base_target_x = mouse_norm_y * MOUSE_SENSITIVITY * 1.5
                base_target_y = mouse_norm_x * MOUSE_SENSITIVITY * 1.5
                base_target_z = mouse_norm_x * MOUSE_SENSITIVITY * 0.75

                # 添加随机变化，增加自然感
                random_factor = noise.pnoise1(t, repeat=1000) * 5
                target_x = base_target_x + random_factor
                target_y = base_target_y + random_factor
                target_z = base_target_z + random_factor * 0.5
                
                # 限制最大幅度
                target_x = max(min(target_x, 30), -30)
                target_y = max(min(target_y, 35), -35)
                target_z = max(min(target_z, 40), -40)
                
                # 调整惯性，实现平滑过渡
                inertia = 0.65  # 更低的惯性，更快的响应
                velocities["x"] = velocities["x"] * inertia + (target_x - current_angles["x"]) * (1 - inertia)
                velocities["y"] = velocities["y"] * inertia + (target_y - current_angles["y"]) * (1 - inertia)
                velocities["z"] = velocities["z"] * inertia + (target_z - current_angles["z"]) * (1 - inertia)
                
                # 平滑速度变化
                velocities["x"] = velocities["x"] * 0.8 + velocities["x"] * 0.2
                velocities["y"] = velocities["y"] * 0.8 + velocities["y"] * 0.2
                velocities["z"] = velocities["z"] * 0.8 + velocities["z"] * 0.2
                
                # 更新角度
                current_angles["x"] += velocities["x"]
                current_angles["y"] += velocities["y"]
                current_angles["z"] += velocities["z"]
                
                # 限制最终角度范围
                current_angles["x"] = max(min(current_angles["x"], 30), -30)
                current_angles["y"] = max(min(current_angles["y"], 35), -35)
                current_angles["z"] = max(min(current_angles["z"], 40), -40)
                
                # 口型配合 - 随机变化的嘴巴张开程度
                mouth_open = 0.3 + random.uniform(0.2, 0.5) * noise.pnoise1(t * 5, repeat=1000)
                mouth_open = max(0.1, min(0.9, mouth_open))  # 限制在0.1到0.9之间
                
                # 构建参数列表
                parameter_values = [
                    {"id": "FaceAngleX", "value": current_angles["x"]},
                    {"id": "FaceAngleY", "value": current_angles["y"]},
                    {"id": "FaceAngleZ", "value": current_angles["z"]},
                    {"id": "EyeOpenLeft", "value": 1.0},
                    {"id": "EyeOpenRight", "value": 1.0},
                    {"id": "EyeLeftX", "value": mouse_norm_x * 0.8},  # 眼球跟随鼠标（增加系数以提高动作幅度）
                    {"id": "EyeRightX", "value": mouse_norm_x * 0.8},
                    {"id": "EyeLeftY", "value": -mouse_norm_y * 0.8},
                    {"id": "EyeRightY", "value": -mouse_norm_y * 0.8},
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