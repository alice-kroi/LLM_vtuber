#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 模型参数可视化工具

显示当前模型的所有参数、当前值和上下限值
"""

import asyncio
import tkinter as tk
from tkinter import ttk
import sys
import os

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vtuber_studio_info import VTubeStudioAPI

class Live2DParamVisualizer:
    """Live2D 模型参数可视化类"""
    
    def __init__(self, host="localhost", port=8001, api=None):
        """初始化参数可视化器
        
        参数:
            host (str): VTube Studio服务器主机名
            port (int): VTube Studio服务器端口
            api (VTubeStudioAPI): 共享的API连接，如果提供则使用，否则创建新连接
        """
        if api:
            self.api = api
        else:
            self.api = VTubeStudioAPI(host=host, port=port)
        self.running = False
        self.parameters = []
        self.root = None
        self.tree = None
        self.update_interval = 1000  # 更新间隔（毫秒）
        self.param_ids = {}  # 存储参数ID和树项ID的映射，用于快速更新
    
    async def connect(self):
        """连接到VTube Studio服务器"""
        return await self.api.connect()
    
    async def login(self, plugin_name="Live2DParamVisualizer", plugin_developer="Developer"):
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
    
    async def get_parameters(self):
        """获取当前模型的参数列表"""
        try:
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
                
                return parameters
            else:
                print("获取参数列表失败: 响应数据无效")
                return []
        except Exception as e:
            print(f"获取参数列表失败: {e}")
            return []
    
    def create_gui(self):
        """创建GUI界面"""
        # 创建主窗口
        self.root = tk.Tk()
        self.root.title("Live2D 模型参数可视化")
        self.root.geometry("1000x600")
        
        # 创建滚动条
        scrollbar = ttk.Scrollbar(self.root)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 创建树状视图
        self.tree = ttk.Treeview(self.root, yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.tree.yview)
        
        # 定义列
        self.tree["columns"] = ("name", "value", "min", "max", "default", "addedBy")
        
        # 设置列宽和标题
        self.tree.column("#0", width=0, stretch=tk.NO)
        self.tree.column("name", width=200, anchor=tk.W)
        self.tree.column("value", width=100, anchor=tk.CENTER)
        self.tree.column("min", width=80, anchor=tk.CENTER)
        self.tree.column("max", width=80, anchor=tk.CENTER)
        self.tree.column("default", width=100, anchor=tk.CENTER)
        self.tree.column("addedBy", width=150, anchor=tk.W)
        
        # 设置列标题
        self.tree.heading("#0", text="", anchor=tk.W)
        self.tree.heading("name", text="参数名称", anchor=tk.W)
        self.tree.heading("value", text="当前值", anchor=tk.CENTER)
        self.tree.heading("min", text="最小值", anchor=tk.CENTER)
        self.tree.heading("max", text="最大值", anchor=tk.CENTER)
        self.tree.heading("default", text="默认值", anchor=tk.CENTER)
        self.tree.heading("addedBy", text="添加者", anchor=tk.W)
    
    def update_gui(self, parameters=None):
        """更新GUI界面
        
        参数:
            parameters (list): 参数列表，如果为None则使用空列表
        """
        if not self.running or not self.tree:
            return
        
        # 使用传入的参数列表或空列表
        if parameters is None:
            parameters = []
        
        # 第一次调用时，初始化树状视图并建立参数映射
        if not self.param_ids:
            # 清空树状视图
            for item in self.tree.get_children():
                self.tree.delete(item)
            
            # 添加参数到树状视图并建立映射
            for param in parameters:
                param_name = param.get("name", "")
                if param_name:
                    item_id = self.tree.insert(
                        "",
                        tk.END,
                        values=(
                            param_name,
                            f"{param.get('value', 0):.3f}",
                            param.get("min", 0),
                            param.get("max", 0),
                            param.get("defaultValue", 0),
                            param.get("addedBy", "")
                        )
                    )
                    self.param_ids[param_name] = item_id
        else:
            # 后续调用时，只更新数值
            for param in parameters:
                param_name = param.get("name", "")
                if param_name in self.param_ids:
                    item_id = self.param_ids[param_name]
                    # 更新数值列
                    self.tree.item(item_id, values=(
                        param_name,
                        f"{param.get('value', 0):.3f}",
                        param.get("min", 0),
                        param.get("max", 0),
                        param.get("defaultValue", 0),
                        param.get("addedBy", "")
                    ))
        

    
    def run_gui(self):
        """运行GUI界面"""
        import threading
        
        def gui_thread():
            try:
                self.create_gui()
                # 初始更新一次
                self.update_gui([])
                self.root.mainloop()
            except Exception as e:
                print(f"GUI线程错误: {e}")
        
        # 在单独的线程中运行GUI
        thread = threading.Thread(target=gui_thread)
        thread.daemon = True
        thread.start()
        # 等待GUI线程启动
        import time
        time.sleep(0.5)
    
    async def run(self):
        """启动参数可视化器"""
        self.running = True
        print("Live2D 参数可视化器已启动")
        
        try:
            # 运行GUI界面
            self.run_gui()
            # 由于GUI在单独的线程中运行，这里可以立即返回
        except Exception as e:
            print(f"可视化器运行出错: {e}")
        finally:
            # 不要在这里设置 self.running = False，因为GUI可能还在运行
            pass
    
    async def stop(self):
        """停止参数可视化器"""
        self.running = False
        if self.root:
            self.root.quit()
        print("Live2D 参数可视化器已停止")


# 使用示例
async def main():
    # 创建参数可视化器实例
    visualizer = Live2DParamVisualizer()
    
    try:
        # 连接服务器
        if not await visualizer.connect():
            return
        
        # 登录认证
        if not await visualizer.login():
            await visualizer.disconnect()
            return
        
        # 启动可视化器
        await visualizer.run()
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        # 断开连接
        await visualizer.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
