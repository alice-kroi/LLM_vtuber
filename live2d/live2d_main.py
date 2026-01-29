#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 主控模块

该模块负责接收 JSON 格式的请求，并调用 live2d_contral 中的相应函数进行处理
"""

import asyncio
import json
import logging
from typing import Dict, Any
import websockets

from live2d_control import Live2DController

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live2d_main")

# 默认配置
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8888
DEFAULT_PLUGIN_NAME = "LLM_vtuber"
DEFAULT_PLUGIN_DEVELOPER = "LLM_vtuber"


class Live2DMainServer:
    """Live2D 主控服务器类"""
    
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, 
                 plugin_name: str = DEFAULT_PLUGIN_NAME, 
                 plugin_developer: str = DEFAULT_PLUGIN_DEVELOPER):
        """初始化主控服务器
        
        Args:
            host (str): 服务器主机地址
            port (int): 服务器端口号
            plugin_name (str): 插件名称
            plugin_developer (str): 插件开发者名称
        """
        self.host = host
        self.port = port
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.server = None
        self.is_running = False
        # 记录已连接的客户端
        self.connected_clients = set()
    
    async def start(self):
        """启动服务器
        
        Returns:
            bool: 服务器是否成功启动
        """
        try:
            # 连接到 VTube Studio 并进行认证
            logger.info("正在连接到 VTube Studio...")
            if not await live2d_controller.connect_and_auth(self.plugin_name, self.plugin_developer):
                logger.error("无法连接到 VTube Studio，服务器启动失败")
                return False
            
            # 创建 WebSocket 服务器
            self.server = await websockets.serve(
                self.handle_client,
                self.host,
                self.port
            )
            
            self.is_running = True
            logger.info(f"Live2D 主控服务器已启动，监听 {self.host}:{self.port}")
            logger.info(f"插件名称: {self.plugin_name}, 开发者: {self.plugin_developer}")
            return True
            
        except Exception as e:
            logger.error(f"启动服务器时发生错误: {e}")
            return False
    
    async def stop(self):
        """停止服务器"""
        try:
            if self.is_running:
                # 关闭 WebSocket 服务器
                if self.server:
                    self.server.close()
                    await self.server.wait_closed()
                
                # 断开与 VTube Studio 的连接
                await live2d_controller.disconnect()
                
                self.is_running = False
                logger.info("Live2D 主控服务器已停止")
                
        except Exception as e:
            logger.error(f"停止服务器时发生错误: {e}")
    
    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """处理客户端连接
        
        Args:
            websocket (websockets.WebSocketServerProtocol): WebSocket 连接对象
        """
        client_address = websocket.remote_address
        logger.info(f"客户端已连接: {client_address}")
        
        # 记录客户端连接
        self.connected_clients.add(websocket)
        
        try:
            async for message in websocket:
                try:
                    # 解析 JSON 请求
                    request_data = json.loads(message)
                    logger.info(f"收到来自 {client_address} 的请求: {request_data}")
                    
                    # 处理请求
                    response = await self.process_request(request_data)
                    
                    # 发送响应
                    await websocket.send(json.dumps(response))
                    logger.info(f"已向 {client_address} 发送响应: {response}")
                    
                except json.JSONDecodeError:
                    # 处理无效的 JSON 格式
                    error_response = {
                        "status": "error",
                        "message": "无效的 JSON 格式",
                        "received_data": message[:100] + "..." if len(message) > 100 else message
                    }
                    await websocket.send(json.dumps(error_response))
                    logger.error(f"来自 {client_address} 的无效 JSON 格式: {message[:100]}...")
                    
                except Exception as e:
                    # 处理其他错误
                    error_response = {
                        "status": "error",
                        "message": f"处理请求时发生错误: {str(e)}",
                        "error_type": type(e).__name__
                    }
                    await websocket.send(json.dumps(error_response))
                    logger.error(f"处理来自 {client_address} 的请求时发生错误: {e}")
                    
        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"客户端已正常断开连接: {client_address}")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"客户端连接异常断开: {client_address}, 错误代码: {e.code}, 原因: {e.reason}")
        except Exception as e:
            logger.error(f"处理客户端 {client_address} 连接时发生错误: {e}")
        finally:
            # 移除客户端连接
            if websocket in self.connected_clients:
                self.connected_clients.remove(websocket)
            logger.info(f"当前已连接客户端数量: {len(self.connected_clients)}")
    
    async def process_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理请求
        
        Args:
            request_data (Dict[str, Any]): 请求数据
            
        Returns:
            Dict[str, Any]: 处理结果
        """
        try:
            # 验证请求的基本结构
            if "command" not in request_data:
                return {
                    "status": "error",
                    "message": "请求缺少 'command' 字段",
                    "request_id": request_data.get("request_id")
                }
            
            command = request_data["command"]
            request_id = request_data.get("request_id", str(asyncio.get_event_loop().time()))
            
            logger.debug(f"处理命令: {command}, 请求ID: {request_id}")
            
            # 统一响应格式
            response = {
                "status": "success",
                "request_id": request_id,
                "command": command,
                "timestamp": asyncio.get_event_loop().time()
            }
            
            # 根据命令类型处理请求
            if command == "handle_event":
                # 验证事件数据的基本结构
                if "event_data" not in request_data:
                    response["status"] = "error"
                    response["message"] = "handle_event 命令缺少 'event_data' 字段"
                    return response
                
                event_data = request_data["event_data"]
                
                # 调用 live2d_contral 中的处理函数
                result = await live2d_controller.handle_event(event_data)
                
                # 合并结果
                response.update(result)
                return response
                
            elif command == "connect":
                # 重新连接到 VTube Studio
                plugin_name = request_data.get("plugin_name", self.plugin_name)
                plugin_developer = request_data.get("plugin_developer", self.plugin_developer)
                
                success = await live2d_controller.connect_and_auth(plugin_name, plugin_developer)
                if success:
                    # 更新服务器配置
                    self.plugin_name = plugin_name
                    self.plugin_developer = plugin_developer
                    
                    response["message"] = "已成功连接到 VTube Studio"
                    response["plugin_info"] = {
                        "name": plugin_name,
                        "developer": plugin_developer
                    }
                else:
                    response["status"] = "error"
                    response["message"] = "连接到 VTube Studio 失败"
                
                return response
                    
            elif command == "disconnect":
                # 断开与 VTube Studio 的连接
                await live2d_controller.disconnect()
                response["message"] = "已断开与 VTube Studio 的连接"
                return response
                
            elif command == "start_animation":
                # 启动动画
                state = request_data.get("state", "idle")
                await live2d_controller.start_animation(state)
                response["message"] = f"已启动 {state} 动画"
                return response
                
            elif command == "stop_animation":
                # 停止动画
                await live2d_controller.stop_animation()
                response["message"] = "已停止动画"
                return response
                
            elif command == "get_status":
                # 获取当前状态
                response["status_info"] = {
                    "is_connected": live2d_controller.is_connected,
                    "is_authenticated": live2d_controller.is_authenticated,
                    "current_state": live2d_controller.current_state,
                    "animation_running": live2d_controller.animation_running,
                    "connected_clients": len(self.connected_clients)
                }
                return response
                
            elif command == "execute_function":
                # 直接执行 live2d_controller 中的函数
                if "function_name" not in request_data:
                    response["status"] = "error"
                    response["message"] = "execute_function 命令缺少 'function_name' 字段"
                    return response
                
                function_name = request_data["function_name"]
                args = request_data.get("args", [])
                kwargs = request_data.get("kwargs", {})
                
                # 检查函数是否存在且可调用
                if hasattr(live2d_controller, function_name) and callable(getattr(live2d_controller, function_name)):
                    function = getattr(live2d_controller, function_name)
                    # 执行函数
                    result = await function(*args, **kwargs)
                    response["result"] = result
                    response["message"] = f"函数 {function_name} 执行成功"
                else:
                    response["status"] = "error"
                    response["message"] = f"未知函数: {function_name}"
                
                return response
                
            else:
                response["status"] = "error"
                response["message"] = f"未知命令: {command}"
                return response
                
        except Exception as e:
            logger.error(f"处理请求时发生错误: {e}")
            return {
                "status": "error",
                "message": f"处理请求时发生错误: {str(e)}",
                "error_type": type(e).__name__,
                "request_id": request_data.get("request_id")
            }
    
    async def run_forever(self):
        """持续运行服务器"""
        if await self.start():
            try:
                # 保持服务器运行
                logger.info("Live2D 主控服务器已准备就绪，可以接收请求")
                await asyncio.Future()
            except KeyboardInterrupt:
                logger.info("收到中断信号，正在停止服务器...")
            finally:
                await self.stop()
                logger.info("Live2D 主控服务器已完全停止")
    
    async def broadcast(self, message: Dict[str, Any]):
        """向所有连接的客户端广播消息
        
        Args:
            message (Dict[str, Any]): 要广播的消息
        """
        if not self.is_running or not self.connected_clients:
            return
            
        message_json = json.dumps(message)
        disconnected_clients = set()
        
        for client in self.connected_clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
        
        # 移除断开连接的客户端
        for client in disconnected_clients:
            if client in self.connected_clients:
                self.connected_clients.remove(client)
        
        logger.debug(f"已向 {len(self.connected_clients)} 个客户端广播消息")


async def main():
    """主函数"""
    # 从命令行参数获取配置
    import argparse
    
    parser = argparse.ArgumentParser(description='Live2D 主控服务器')
    parser.add_argument('--host', type=str, default=DEFAULT_HOST, help='服务器主机地址')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='服务器端口号')
    parser.add_argument('--plugin-name', type=str, default=DEFAULT_PLUGIN_NAME, help='插件名称')
    parser.add_argument('--plugin-developer', type=str, default=DEFAULT_PLUGIN_DEVELOPER, help='插件开发者名称')
    parser.add_argument('--log-level', type=str, default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help='日志级别')
    
    args = parser.parse_args()
    
    # 配置日志级别
    logging.basicConfig(level=getattr(logging, args.log_level))
    logger.setLevel(getattr(logging, args.log_level))
    
    # 创建并启动服务器
    logger.info(f"正在初始化 Live2D 主控服务器...")
    logger.info(f"配置: 主机={args.host}, 端口={args.port}, 插件名称={args.plugin_name}, 开发者={args.plugin_developer}")
    
    server = Live2DMainServer(
        host=args.host,
        port=args.port,
        plugin_name=args.plugin_name,
        plugin_developer=args.plugin_developer
    )
    
    await server.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止服务器...")
    except Exception as e:
        logger.error(f"服务器运行时发生错误: {e}")