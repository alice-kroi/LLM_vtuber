#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 模型控制程序

根据 design.md 文件的需求实现：
1. 连接 live2d 模型
2. 控制模型运动
   2.0：程序实际上的原理是持续发送动作参数，下面的指令都是把对应参数的变化量一起整合进去实现的
   2.1：模型应该有个常态的大型不规则运行
   2.2：模型应该可以根据指令，往特定上下左右中等9个方向看去的动作
   2.3：模型应该选择张嘴和关闭
"""

import asyncio
import random
import noise
import time
import sys
import os
import argparse

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d.vtuber_studio_info import VTubeStudioAPI

class Live2DMain:
    """Live2D 模型控制主类"""
    
    def __init__(self, host="localhost", port=8001):
        """初始化控制器
        
        参数:
            host (str): VTube Studio服务器主机名
            port (int): VTube Studio服务器端口
        """
        self.api = VTubeStudioAPI(host=host, port=port)
        self.running = False
        self.current_direction = "downleft"  # 当前朝向：center, up, down, left, right, upleft, upright, downleft, downright
        self.mouth_open = False  # 嘴巴是否张开
        self.t = 0  # 时间变量，用于生成随机运动
        self.visualizer = None  # 参数可视化器
        self.param_ranges = {}  # 存储参数的最大最小值和默认值
        self.param_mappings = {}  # 存储参数的映射信息
        self.core_params = {}  # 核心参数，存储当前状态
        self.start_time = 0  # 开始时间，用于常态晃动
        
        # 指令队列（用于接收外部指令）
        self.command_queue = asyncio.Queue()
        # 全局操作锁（确保只有一个操作在进行，防止WebSocket并发冲突）
        self.operation_lock = asyncio.Lock()
        # 是否正在执行指令
        self.executing_command = False
    
    def map_parameter(self, param_name, value):
        """
        将参数值映射到标准范围
        
        参数:
            param_name (str): 参数名称
            value (float): 参数值
            
        返回:
            float: 映射后的值
        """
        if param_name not in self.param_ranges:
            return value
        
        # 获取参数范围
        min_val = self.param_ranges[param_name]["min"]
        max_val = self.param_ranges[param_name]["max"]
        default_val = self.param_ranges[param_name]["default"]
        
        # 确定映射类型
        if default_val == min_val or default_val == max_val:
            # 1.2.1: 默认值与最大或最小值相同，映射到0-1
            if default_val == min_val:
                # 默认值是最小值，映射到0
                if max_val == min_val:
                    return 0
                return (value - min_val) / (max_val - min_val)
            else:
                # 默认值是最大值，映射到1
                if max_val == min_val:
                    return 1
                return 1 - (value - min_val) / (max_val - min_val)
        else:
            # 1.2.2: 默认值在中间，映射到-1~1
            if max_val == min_val:
                return 0
            return 2 * (value - min_val) / (max_val - min_val) - 1
    
    def unmap_parameter(self, param_name, mapped_value):
        """
        将映射值反映射到实际参数范围
        
        参数:
            param_name (str): 参数名称
            mapped_value (float): 映射后的值
            
        返回:
            float: 反映射后的值
        """
        if param_name not in self.param_ranges:
            return mapped_value
        
        # 获取参数范围
        min_val = self.param_ranges[param_name]["min"]
        max_val = self.param_ranges[param_name]["max"]
        default_val = self.param_ranges[param_name]["default"]
        
        # 确定映射类型
        if default_val == min_val or default_val == max_val:
            # 1.2.1: 映射到0-1
            if default_val == min_val:
                # 默认值是最小值，映射到0
                return min_val + mapped_value * (max_val - min_val)
            else:
                # 默认值是最大值，映射到1
                return max_val - mapped_value * (max_val - min_val)
        else:
            # 1.2.2: 映射到-1~1
            return min_val + (mapped_value + 1) * (max_val - min_val) / 2
    
    def get_irregular_shake(self, current_time, duration=2):
        """
        获取不规则晃动值
        
        参数:
            current_time (float): 当前时间
            duration (float): 晃动持续时间
            
        返回:
            float: 晃动值，范围在-1到1之间
        """
        import math
        
        # 计算当前时间在周期中的位置
        # 默认为2倍周期，所以总周期是duration
        t = current_time % duration
        
        # 使用正弦函数生成晃动，范围在-1到1之间
        # 2倍周期，所以使用2*pi
        return math.sin(2 * math.pi * t / duration)
    
    def get_irregular_shake_dict(self, current_time, duration=2):
        """
        获取不规则晃动的参数字典
        
        参数:
            current_time (float): 当前时间
            duration (float): 晃动持续时间
            
        返回:
            dict: 晃动参数字典
        """
        shake_value = self.get_irregular_shake(current_time, duration)
        
        # 生成晃动参数字典
        # 这里只处理面部角度和位置参数
        shake_params = {
            "FaceAngleX": shake_value * 0.2,  # 上下晃动
            "FaceAngleY": shake_value * 0.2,  # 左右晃动
            "FaceAngleZ": shake_value * 0.1,  # 左右倾斜
            "FacePositionX": shake_value * 0.1,  # 左右移动
            "FacePositionY": shake_value * 0.1   # 上下移动
        }
        
        return shake_params
    
    def get_normal_shake(self, current_time):
        """
        获取常态晃动值
        
        参数:
            current_time (float): 当前时间
            
        返回:
            float: 晃动值，范围在-1到1之间
        """
        import math
        
        # 使用正弦函数生成常态晃动，范围在-1到1之间
        # 周期为10秒，使晃动更加自然
        return math.sin(2 * math.pi * current_time / 10)
    
    def get_normal_shake_dict(self, current_time):
        """
        获取常态晃动的参数字典
        
        参数:
            current_time (float): 当前时间
            
        返回:
            dict: 晃动参数字典
        """
        shake_value = self.get_normal_shake(current_time)
        
        # 生成晃动参数字典
        # 这里只处理面部角度和位置参数
        shake_params = {
            "FaceAngleX": shake_value * 0.1,  # 上下晃动
            "FaceAngleY": shake_value * 0.1,  # 左右晃动
            "FaceAngleZ": shake_value * 0.05,  # 左右倾斜
            "FacePositionX": shake_value * 0.05,  # 左右移动
            "FacePositionY": shake_value * 0.05   # 上下移动
        }
        
        return shake_params
    
    async def move(self, target_params, duration=1):
        """
        移动功能
        
        参数:
            target_params (dict): 目标参数字典，值为映射后的值
            duration (float): 移动持续时间（秒）
        """
        import time
        
        # 记录开始时间
        start_time = time.time()
        
        # 确保核心参数已初始化
        if not self.core_params:
            # 初始化核心参数为默认值
            for param_name in self.param_ranges:
                default_val = self.param_ranges[param_name]["default"]
                self.core_params[param_name] = self.map_parameter(param_name, default_val)
        
        # 计算参数差值
        param_diff = {}
        for param_name, target_value in target_params.items():
            if param_name in self.core_params:
                param_diff[param_name] = target_value - self.core_params[param_name]
            else:
                # 如果核心参数中没有该参数，初始化为0
                self.core_params[param_name] = 0
                param_diff[param_name] = target_value
        
        # 执行移动过程
        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time
            
            # 检查是否到达目标时间
            if elapsed_time >= duration:
                break
            
            # 计算当前进度（0到1之间）
            progress = elapsed_time / duration
            
            # 计算当前参数值
            current_params = {}
            for param_name, diff in param_diff.items():
                # 核心数值 + 差值 * 进度 + 不规则晃动 + 常态晃动
                base_value = self.core_params.get(param_name, 0) + diff * progress
                
                # 添加不规则晃动
                irregular_shake = self.get_irregular_shake_dict(elapsed_time).get(param_name, 0)
                
                # 添加常态晃动
                normal_shake = self.get_normal_shake_dict(current_time - self.start_time).get(param_name, 0)
                
                # 计算最终值
                current_params[param_name] = base_value + irregular_shake + normal_shake
            
            # 反映射参数并发送
            parameter_values = []
            for param_name, mapped_value in current_params.items():
                actual_value = self.unmap_parameter(param_name, mapped_value)
                parameter_values.append({"id": param_name, "value": actual_value})
            
            # 发送参数数据
            await self.inject_parameter_data(parameter_values, face_found=True, mode="set")
            
            # 等待一小段时间
            await asyncio.sleep(0.1)
        
        # 移动完成后，更新核心参数
        for param_name, target_value in target_params.items():
            self.core_params[param_name] = target_value
        
        # 发送最终参数值（包含常态晃动）
        final_params = {}
        current_time = time.time()
        for param_name, target_value in target_params.items():
            # 目标值 + 常态晃动
            normal_shake = self.get_normal_shake_dict(current_time - self.start_time).get(param_name, 0)
            final_params[param_name] = target_value + normal_shake
        
        # 反映射并发送最终参数
        parameter_values = []
        for param_name, mapped_value in final_params.items():
            actual_value = self.unmap_parameter(param_name, mapped_value)
            parameter_values.append({"id": param_name, "value": actual_value})
        
        await self.inject_parameter_data(parameter_values, face_found=True, mode="set")
        
        print(f"移动完成，耗时 {duration} 秒")
    
    async def update_non_moving_state(self, current_time):
        """
        非移动状态处理
        
        参数:
            current_time (float): 当前时间
        """
        # 确保核心参数已初始化
        if not self.core_params:
            # 初始化核心参数为默认值
            for param_name in self.param_ranges:
                default_val = self.param_ranges[param_name]["default"]
                self.core_params[param_name] = self.map_parameter(param_name, default_val)
        
        # 计算当前参数值（核心参数 + 常态晃动）
        current_params = {}
        for param_name, core_value in self.core_params.items():
            # 核心值 + 常态晃动
            normal_shake = self.get_normal_shake_dict(current_time - self.start_time).get(param_name, 0)
            current_params[param_name] = core_value + normal_shake
        
        # 反映射参数并发送
        parameter_values = []
        for param_name, mapped_value in current_params.items():
            actual_value = self.unmap_parameter(param_name, mapped_value)
            parameter_values.append({"id": param_name, "value": actual_value})
        
        # 发送参数数据
        await self.inject_parameter_data(parameter_values, face_found=True, mode="set")
    
    async def move_to_direction(self, direction, duration=1):
        """
        移动到指定方向
        
        参数:
            direction (str): 方向，可选值：center, up, down, left, right, upleft, upright, downleft, downright
            duration (float): 移动持续时间（秒）
        """
        # 定义方向模板（符合3D标准坐标系）
        # X轴: 水平方向（左右）
        # Y轴: 垂直方向（上下）
        # Z轴: 垂直于XY平面（深度/倾斜）
        direction_templates = {
            "center": {
                "FaceAngleX": 0,      # X轴旋转（水平方向）
                "FaceAngleY": 0,      # Y轴旋转（垂直方向）
                "FaceAngleZ": 0,      # Z轴旋转（深度倾斜）
                "FacePositionX": 0,   # X轴位置（水平移动）
                "FacePositionY": 0    # Y轴位置（垂直移动）
            },
            "up": {
                "FaceAngleX": 0,      # X轴：无水平旋转
                "FaceAngleY": 0.5,    # Y轴：向上旋转（抬头）
                "FaceAngleZ": 0,      # Z轴：无深度倾斜
                "FacePositionX": 0,   # X轴：无水平移动
                "FacePositionY": 0.2  # Y轴：向上移动
            },
            "down": {
                "FaceAngleX": 0,       # X轴：无水平旋转
                "FaceAngleY": -0.5,    # Y轴：向下旋转（低头）
                "FaceAngleZ": 0,       # Z轴：无深度倾斜
                "FacePositionX": 0,    # X轴：无水平移动
                "FacePositionY": -0.2  # Y轴：向下移动
            },
            "left": {
                "FaceAngleX": -0.5,    # X轴：向左旋转（左转头）
                "FaceAngleY": 0,       # Y轴：无垂直旋转
                "FaceAngleZ": -0.3,    # Z轴：向左倾斜（左歪头）
                "FacePositionX": -0.2, # X轴：向左移动
                "FacePositionY": 0     # Y轴：无垂直移动
            },
            "right": {
                "FaceAngleX": 0.5,     # X轴：向右旋转（右转头）
                "FaceAngleY": 0,       # Y轴：无垂直旋转
                "FaceAngleZ": 0.3,     # Z轴：向右倾斜（右歪头）
                "FacePositionX": 0.2,  # X轴：向右移动
                "FacePositionY": 0     # Y轴：无垂直移动
            },
            "upleft": {
                "FaceAngleX": -0.4,    # X轴：向左旋转
                "FaceAngleY": 0.4,     # Y轴：向上旋转
                "FaceAngleZ": -0.2,    # Z轴：向左倾斜
                "FacePositionX": -0.15, # X轴：向左移动
                "FacePositionY": 0.15  # Y轴：向上移动
            },
            "upright": {
                "FaceAngleX": 0.4,      # X轴：向右旋转
                "FaceAngleY": 0.4,     # Y轴：向上旋转
                "FaceAngleZ": 0.2,      # Z轴：向右倾斜
                "FacePositionX": 0.15,  # X轴：向右移动
                "FacePositionY": 0.15   # Y轴：向上移动
            },
            "downleft": {
                "FaceAngleX": -0.4,     # X轴：向左旋转
                "FaceAngleY": -0.4,     # Y轴：向下旋转
                "FaceAngleZ": -0.2,     # Z轴：向左倾斜
                "FacePositionX": -0.15,  # X轴：向左移动
                "FacePositionY": -0.15   # Y轴：向下移动
            },
            "downright": {
                "FaceAngleX": 0.4,       # X轴：向右旋转
                "FaceAngleY": -0.4,      # Y轴：向下旋转
                "FaceAngleZ": 0.2,       # Z轴：向右倾斜
                "FacePositionX": 0.15,   # X轴：向右移动
                "FacePositionY": -0.15   # Y轴：向下移动
            }
        }
        
        # 检查方向是否有效
        if direction not in direction_templates:
            print(f"无效的方向: {direction}")
            return False
        
        # 获取方向模板
        target_params = direction_templates[direction]
        
        # 设置执行标志，防止与常态运动冲突
        self.executing_command = True
        try:
            # 执行移动（持有锁防止并发冲突）
            async with self.operation_lock:
                await self.move(target_params, duration)
        finally:
            # 清除执行标志
            self.executing_command = False
        
        # 更新当前方向
        self.current_direction = direction
        
        print(f"已移动到方向: {direction}")
        return True
    
    async def connect(self):
        """连接到VTube Studio服务器"""
        return await self.api.connect()
    
    async def initialize(self):
        """统一初始化方法"""
        print("开始初始化...")
        
        # 获取参数范围
        if not await self.get_parameter_ranges():
            print("初始化失败：无法获取参数范围")
            return False
        
        # 初始化核心参数
        if not self.core_params:
            # 初始化核心参数为默认值
            for param_name in self.param_ranges:
                default_val = self.param_ranges[param_name]["default"]
                self.core_params[param_name] = self.map_parameter(param_name, default_val)
            print(f"核心参数初始化完成，共 {len(self.core_params)} 个参数")
        
        # 初始化开始时间
        self.start_time = time.time()
        
        print("初始化完成")
        return True
    
    async def login(self, plugin_name="Live2DMain", plugin_developer="Developer"):
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
    
    async def get_parameter_ranges(self):
        """获取模型参数的最大最小值和默认值"""
        try:
            response = await self.api.get_tracking_parameters()
            if response and "data" in response:
                data = response["data"]
                
                # 获取默认参数的范围和默认值
                if "defaultParameters" in data:
                    for param in data["defaultParameters"]:
                        param_name = param.get("name")
                        if param_name:
                            self.param_ranges[param_name] = {
                                "min": param.get("min", 0),
                                "max": param.get("max", 0),
                                "default": param.get("defaultValue", 0)
                            }
                
                # 获取自定义参数的范围和默认值
                if "customParameters" in data:
                    for param in data["customParameters"]:
                        param_name = param.get("name")
                        if param_name:
                            self.param_ranges[param_name] = {
                                "min": param.get("min", 0),
                                "max": param.get("max", 0),
                                "default": param.get("defaultValue", 0)
                            }
                
                print(f"获取到 {len(self.param_ranges)} 个参数的范围和默认值")
                return True
            else:
                print("获取参数范围失败: 响应数据无效")
                return False
        except Exception as e:
            print(f"获取参数范围失败: {e}")
            return False
    
    async def set_direction(self, direction):
        """设置模型朝向
        
        参数:
            direction (str): 朝向方向，可选值：center, up, down, left, right, upleft, upright, downleft, downright
        """
        valid_directions = ["center", "up", "down", "left", "right", "upleft", "upright", "downleft", "downright"]
        if direction not in valid_directions:
            print(f"无效的方向: {direction}，请使用以下方向之一: {valid_directions}")
            return False
        
        self.current_direction = direction
        print(f"模型朝向已设置为: {direction}")
        return True
    
    async def set_mouth_state(self, open_state):
        """设置嘴巴状态
        
        参数:
            open_state (bool): True表示张嘴，False表示闭嘴
        """
        # 设置执行标志，防止与常态运动冲突
        self.executing_command = True
        try:
            self.mouth_open = open_state
            
            # 立即发送嘴巴参数到 VTube Studio
            # 根据嘴巴状态设置参数值
            mouth_value = 1.0 if open_state else 0.0
            
            # 尝试查找嘴巴参数
            mouth_param_name = None
            for param_name in self.param_ranges.keys():
                if "mouth" in param_name.lower() or "嘴" in param_name:
                    mouth_param_name = param_name
                    break
            
            if mouth_param_name:
                # 映射参数值
                mapped_value = self.map_parameter(mouth_param_name, mouth_value)
                actual_value = self.unmap_parameter(mouth_param_name, mapped_value)
                
                # 发送参数数据
                parameter_values = [{"id": mouth_param_name, "value": actual_value}]
                await self.inject_parameter_data(parameter_values, face_found=True, mode="set")
                print(f"已发送嘴巴参数: {mouth_param_name} = {actual_value}")
            else:
                print(f"警告: 未找到嘴巴参数，嘴巴状态可能不会变化")
            
            print(f"嘴巴状态已设置为: {'张开' if open_state else '关闭'}")
        finally:
            # 清除执行标志
            self.executing_command = False
        
        return True
    
    async def inject_parameter_data(self, parameter_values, face_found=True, mode="set"):
        """注入参数数据到VTube Studio
        
        参数:
            parameter_values (list): 参数值列表，每个元素为字典，包含id和value
            face_found (bool): 是否找到人脸
            mode (str): 模式，可选值：set, add, multiply
        """
        try:
            # 构建请求数据
            data = {
                "parameterValues": parameter_values,
                "faceFound": face_found,
                "mode": mode
            }
            
            if self.api.auth_token:
                data["authenticationToken"] = self.api.auth_token
            
            # 发送请求
            response = await self.api.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="InjectParameterDataRequest",
                data=data
            )
            
            return response
        except Exception as e:
            print(f"注入参数数据失败: {e}")
            return None
    
    async def idle_movement(self):
        """实现常态的大型不规则运行"""
        # 初始化当前时间
        current_time = time.time()
        
        while self.running:
            try:
                # 如果正在执行指令，跳过本次循环，避免冲突导致抽搐
                if self.executing_command:
                    await asyncio.sleep(0.05)
                    continue
                
                # 获取操作锁，确保与指令执行不冲突
                async with self.operation_lock:
                    # 更新当前时间
                    current_time = time.time()
                    
                    # 执行非移动状态处理（核心参数 + 常态晃动）
                    await self.update_non_moving_state(current_time)
                
                # 每次循环更新一次参数可视化（约0.1秒一次）
                # 如果启用了参数可视化，获取真实参数并更新界面
                if self.visualizer:
                    try:
                        # 获取真实参数
                        response = await self.api.get_tracking_parameters()
                        if response and "data" in response:
                            data = response["data"]
                            parameters = []
                            
                            # 获取默认参数
                            if "defaultParameters" in data:
                                for param in data["defaultParameters"]:
                                    parameters.append({
                                        "name": param.get("name"),
                                        "value": param.get("value", 0),
                                        "min": param.get("min", 0),
                                        "max": param.get("max", 0),
                                        "defaultValue": param.get("defaultValue", 0),
                                        "addedBy": "VTube Studio"
                                    })
                            
                            # 获取自定义参数
                            if "customParameters" in data:
                                for param in data["customParameters"]:
                                    parameters.append({
                                        "name": param.get("name"),
                                        "value": param.get("value", 0),
                                        "min": param.get("min", 0),
                                        "max": param.get("max", 0),
                                        "defaultValue": param.get("defaultValue", 0),
                                        "addedBy": param.get("addedBy", "Unknown")
                                    })
                            
                            # 更新可视化界面
                            self.visualizer.update_gui(parameters)
                    except Exception as e:
                        print(f"更新参数可视化失败: {e}")
                
                # 等待一小段时间
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"执行常态运动失败: {e}")
                await asyncio.sleep(1)
    
    async def add_command(self, command):
        """向指令队列添加指令
        
        参数:
            command (dict): 指令字典，包含action和参数
                示例: {"action": "move_to_direction", "direction": "center", "duration": 1}
                      {"action": "set_mouth", "state": True}
                      {"action": "stop"}
        """
        await self.command_queue.put(command)
        print(f"已添加指令: {command}")
    
    async def process_commands(self):
        """处理指令队列中的指令"""
        while self.running:
            try:
                # 尝试获取指令（非阻塞）
                try:
                    command = self.command_queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
                    continue
                
                # 处理指令
                self.executing_command = True
                try:
                    await self.execute_command(command)
                finally:
                    self.executing_command = False
                
                # 标记指令完成
                self.command_queue.task_done()
            except Exception as e:
                print(f"处理指令失败: {e}")
                await asyncio.sleep(0.1)
    
    async def execute_command(self, command):
        """执行单个指令
        
        参数:
            command (dict): 指令字典
        """
        action = command.get("action", "").lower()
        
        if action == "move_to_direction":
            direction = command.get("direction", "center")
            duration = command.get("duration", 1.5)
            print(f"执行指令: 移动到方向 {direction}")
            await self.move_to_direction(direction, duration)
        
        elif action == "set_direction":
            direction = command.get("direction", "center")
            print(f"执行指令: 设置方向 {direction}")
            await self.set_direction(direction)
        
        elif action == "set_mouth":
            state = command.get("state", False)
            print(f"执行指令: 设置嘴巴状态 {state}")
            await self.set_mouth_state(state)
        
        elif action == "open_mouth":
            print("执行指令: 张开嘴巴")
            await self.set_mouth_state(True)
        
        elif action == "close_mouth":
            print("执行指令: 关闭嘴巴")
            await self.set_mouth_state(False)
        
        elif action == "idle":
            print("执行指令: 恢复常态运动")
            # 回到中心位置
            await self.move_to_direction("center", duration=0.5)
        
        elif action == "stop":
            print("执行指令: 停止控制器")
            self.running = False
        
        else:
            print(f"未知指令: {action}")
    
    async def run(self):
        """启动控制器，开始运行（持续监听模式）"""
        self.running = True
        print("Live2D控制器已启动（持续监听模式）")
        print("等待接收指令...")
        
        try:
            # 创建任务：常态运动和指令处理
            tasks = [
                asyncio.create_task(self.idle_movement()),
                asyncio.create_task(self.process_commands())
            ]
            
            # 等待所有任务完成
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"控制器运行出错: {e}")
        finally:
            self.running = False
    
    async def stop(self):
        """停止控制器"""
        self.running = False
        print("Live2D控制器已停止")


# 全局控制器实例（用于外部访问）
live2d_controller = None

async def initialize_live2d(host="localhost", port=8001, visualize=True):
    """初始化Live2D控制器（供外部调用）
    
    参数:
        host (str): VTube Studio服务器主机名
        port (int): VTube Studio服务器端口
        visualize (bool): 是否启动可视化界面
    
    返回:
        Live2DMain: 控制器实例，如果初始化失败返回None
    """
    global live2d_controller
    
    try:
        # 创建控制器实例
        live2d_controller = Live2DMain(host=host, port=port)
        
        # 连接服务器
        if not await live2d_controller.connect():
            print("连接VTube Studio服务器失败")
            return None
        
        # 登录认证
        if not await live2d_controller.login():
            await live2d_controller.disconnect()
            print("登录VTube Studio失败")
            return None
        
        # 统一初始化
        if not await live2d_controller.initialize():
            await live2d_controller.disconnect()
            print("初始化失败")
            return None
        
        # 设置初始嘴巴状态
        await live2d_controller.set_mouth_state(False)
        
        # 如果需要启动可视化界面
        if visualize:
            try:
                from live2d_param_visualizer import Live2DParamVisualizer
                # 创建参数可视化器
                visualizer = Live2DParamVisualizer()
                # 保存visualizer引用到控制器实例
                live2d_controller.visualizer = visualizer
                # 在后台运行可视化器
                asyncio.create_task(visualizer.run())
                
                # 立即更新一次可视化数据
                response = await live2d_controller.api.get_tracking_parameters()
                if response and "data" in response:
                    data = response["data"]
                    parameters = []
                    
                    if "defaultParameters" in data:
                        for param in data["defaultParameters"]:
                            parameters.append({
                                "name": param.get("name"),
                                "value": param.get("value", 0),
                                "min": param.get("min", 0),
                                "max": param.get("max", 0),
                                "defaultValue": param.get("defaultValue", 0),
                                "addedBy": "VTube Studio"
                            })
                    
                    if "customParameters" in data:
                        for param in data["customParameters"]:
                            parameters.append({
                                "name": param.get("name"),
                                "value": param.get("value", 0),
                                "min": param.get("min", 0),
                                "max": param.get("max", 0),
                                "defaultValue": param.get("defaultValue", 0),
                                "addedBy": param.get("addedBy", "Unknown")
                            })
                    
                    visualizer.update_gui(parameters)
                    print("可视化界面初始化完成")
            except Exception as e:
                print(f"初始化可视化失败: {e}")
        
        print("Live2D控制器初始化完成")
        return live2d_controller
    
    except Exception as e:
        print(f"初始化Live2D控制器失败: {e}")
        if live2d_controller:
            await live2d_controller.disconnect()
        return None

async def send_command(command):
    """向Live2D控制器发送指令（供外部调用）
    
    参数:
        command (dict): 指令字典
    
    返回:
        bool: 是否成功发送
    """
    if live2d_controller and live2d_controller.running:
        await live2d_controller.add_command(command)
        return True
    else:
        print("Live2D控制器未启动或未初始化")
        return False

# LangGraph兼容的节点函数
def live2d_node(state):
    """LangGraph兼容的Live2D控制节点
    
    输入:
        state (dict): LangGraph状态，包含以下字段：
            - visual_focus: str, 视觉焦点/方向（center/up/down/left/right/upleft/upright/downleft/downright）
            - action: str, 动作类型（open_mouth/close_mouth/idle/空字符串），默认为空
    
    输出:
        dict: 更新后的状态，包含以下字段：
            - live2d_status: str, 状态（success/error）
            - live2d_message: str, 执行消息
    """
    global live2d_controller
    
    if not live2d_controller or not live2d_controller.running:
        print("Live2D控制器未启动")
        state["live2d_status"] = "error"
        state["live2d_message"] = "Live2D控制器未启动"
        return state
    
    try:
        # 获取视觉焦点（方向）
        visual_focus = state.get("visual_focus", "").strip().lower()
        
        # 获取动作（默认为空）
        action = state.get("action", "").strip().lower()
        
        # 验证方向是否有效
        valid_directions = ["center", "up", "down", "left", "right", "upleft", "upright", "downleft", "downright"]
        
        # 如果有视觉焦点，发送移动指令
        if visual_focus and visual_focus in valid_directions:
            # 使用线程安全的方式发送指令
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(live2d_controller.add_command({
                "action": "move_to_direction",
                "direction": visual_focus,
                "duration": 1.5  # 增加移动时间，使动作更平滑
            }))
            loop.close()
            state["live2d_status"] = "success"
            state["live2d_message"] = f"已发送视觉焦点指令: {visual_focus}"
        
        # 如果有动作指令，执行动作
        if action:
            if action == "open_mouth":
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(live2d_controller.add_command({"action": "open_mouth"}))
                loop.close()
                state["live2d_status"] = "success"
                state["live2d_message"] = "已发送张开嘴巴指令"
            
            elif action == "close_mouth":
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(live2d_controller.add_command({"action": "close_mouth"}))
                loop.close()
                state["live2d_status"] = "success"
                state["live2d_message"] = "已发送关闭嘴巴指令"
            
            elif action == "idle":
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(live2d_controller.add_command({"action": "idle"}))
                loop.close()
                state["live2d_status"] = "success"
                state["live2d_message"] = "已发送恢复常态指令"
        
        # 如果既没有视觉焦点也没有动作，尝试从response中提取语气
        if not visual_focus and not action:
            response = state.get("response", "")
            if response and response.startswith("【"):
                end_bracket = response.find("】")
                if end_bracket != -1:
                    tone = response[1:end_bracket]
                    # 根据语气设置动作
                    action_map = {
                        "开心": "open_mouth",
                        "惊喜": "open_mouth",
                        "调皮": "open_mouth",
                        "撩拨": "open_mouth",
                        "撒娇": "open_mouth",
                        "生气": "close_mouth",
                        "严肃": "close_mouth",
                        "难过": "close_mouth",
                        "疑问": "idle",
                        "尴尬": "idle",
                    }
                    action_type = action_map.get(tone, "idle")
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(live2d_controller.add_command({"action": action_type}))
                    loop.close()
                    state["live2d_status"] = "success"
                    state["live2d_message"] = f"根据语气'{tone}'自动执行动作: {action_type}"
                else:
                    state["live2d_status"] = "success"
                    state["live2d_message"] = "未指定视觉焦点和动作，保持常态"
            else:
                state["live2d_status"] = "success"
                state["live2d_message"] = "未指定视觉焦点和动作，保持常态"
        
        return state
    
    except Exception as e:
        state["live2d_status"] = "error"
        state["live2d_message"] = str(e)
        print(f"Live2D节点执行失败: {e}")
        return state

# 使用示例（持续监听模式）
async def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="Live2D 模型控制程序（持续监听模式）")
    parser.add_argument("--no-visualize", action="store_true", help="不启动参数可视化界面")
    parser.add_argument("--host", type=str, default="localhost", help="VTube Studio服务器主机")
    parser.add_argument("--port", type=int, default=8001, help="VTube Studio服务器端口")
    args = parser.parse_args()
    
    # 初始化控制器
    controller = await initialize_live2d(
        host=args.host,
        port=args.port,
        visualize=not args.no_visualize
    )
    
    if not controller:
        print("初始化失败，退出程序")
        return
    
    try:
        # 启动控制器（持续监听模式）
        await controller.run()
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        # 断开连接
        await controller.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
