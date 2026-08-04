#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 控制器集成测试程序
兼容 LangGraph，可作为 skill 使用
"""

import asyncio
import sys
import os
import argparse

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d.live2d_main import Live2DMain, live2d_node, initialize_live2d, send_command

async def test_direct_commands():
    """测试直接发送指令"""
    print("=== 测试直接发送指令 ===")
    
    # 创建控制器实例
    controller = Live2DMain()
    
    try:
        # 连接服务器
        if not await controller.connect():
            print("连接失败")
            return
        
        # 登录认证
        if not await controller.login():
            await controller.disconnect()
            return
        
        # 初始化
        if not await controller.initialize():
            await controller.disconnect()
            return
        
        # 设置初始嘴巴状态
        await controller.set_mouth_state(False)
        
        # 启动控制器（在后台运行）
        controller.running = True
        idle_task = asyncio.create_task(controller.idle_movement())
        command_task = asyncio.create_task(controller.process_commands())
        
        # 等待初始化完成
        await asyncio.sleep(1)
        
        # 测试发送指令
        print("\n测试移动指令...")
        await controller.add_command({"action": "move_to_direction", "direction": "center", "duration": 1})
        await asyncio.sleep(1.5)
        
        print("\n测试移动到上方...")
        await controller.add_command({"action": "move_to_direction", "direction": "up", "duration": 1})
        await asyncio.sleep(1.5)
        
        print("\n测试移动到右上方...")
        await controller.add_command({"action": "move_to_direction", "direction": "upright", "duration": 1})
        await asyncio.sleep(1.5)
        
        print("\n测试张开嘴巴...")
        await controller.add_command({"action": "open_mouth"})
        await asyncio.sleep(1)
        
        print("\n测试关闭嘴巴...")
        await controller.add_command({"action": "close_mouth"})
        await asyncio.sleep(1)
        
        print("\n测试恢复常态...")
        await controller.add_command({"action": "idle"})
        await asyncio.sleep(2)
        
        # 停止控制器
        controller.running = False
        await asyncio.gather(idle_task, command_task, return_exceptions=True)
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await controller.disconnect()

async def test_langgraph_node():
    """测试 LangGraph 兼容节点"""
    print("\n=== 测试 LangGraph 兼容节点 ===")
    
    # 创建控制器实例
    controller = Live2DMain()
    
    try:
        # 连接服务器
        if not await controller.connect():
            print("连接失败")
            return
        
        # 登录认证
        if not await controller.login():
            await controller.disconnect()
            return
        
        # 初始化
        if not await controller.initialize():
            await controller.disconnect()
            return
        
        # 设置初始嘴巴状态
        await controller.set_mouth_state(False)
        
        # 启动控制器（在后台运行）
        controller.running = True
        idle_task = asyncio.create_task(controller.idle_movement())
        command_task = asyncio.create_task(controller.process_commands())
        
        # 等待初始化完成
        await asyncio.sleep(1)
        
        # 设置全局控制器实例（用于 live2d_node）
        from live2d.live2d_main import live2d_controller
        live2d_controller = controller
        
        # 测试 LangGraph 节点
        print("\n测试 move_to_direction 动作...")
        state = {
            "live2d_action": "move_to_direction",
            "live2d_direction": "center",
            "live2d_duration": 1.0
        }
        result = live2d_node(state)
        print(f"状态更新: {result}")
        await asyncio.sleep(1.5)
        
        print("\n测试 open_mouth 动作...")
        state = {"live2d_action": "open_mouth"}
        result = live2d_node(state)
        print(f"状态更新: {result}")
        await asyncio.sleep(1)
        
        print("\n测试 close_mouth 动作...")
        state = {"live2d_action": "close_mouth"}
        result = live2d_node(state)
        print(f"状态更新: {result}")
        await asyncio.sleep(1)
        
        print("\n测试根据语气自动执行动作...")
        state = {"response": "【开心】今天天气真好！"}
        result = live2d_node(state)
        print(f"状态更新: {result}")
        await asyncio.sleep(2)
        
        print("\n测试另一种语气...")
        state = {"response": "【生气】你怎么这样！"}
        result = live2d_node(state)
        print(f"状态更新: {result}")
        await asyncio.sleep(2)
        
        # 停止控制器
        controller.running = False
        await asyncio.gather(idle_task, command_task, return_exceptions=True)
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await controller.disconnect()

async def test_simulated_dialog():
    """测试模拟对话场景"""
    print("\n=== 测试模拟对话场景 ===")
    
    # 创建控制器实例
    controller = Live2DMain()
    
    try:
        # 连接服务器
        if not await controller.connect():
            print("连接失败")
            return
        
        # 登录认证
        if not await controller.login():
            await controller.disconnect()
            return
        
        # 初始化
        if not await controller.initialize():
            await controller.disconnect()
            return
        
        # 设置初始嘴巴状态
        await controller.set_mouth_state(False)
        
        # 启动控制器（在后台运行）
        controller.running = True
        idle_task = asyncio.create_task(controller.idle_movement())
        command_task = asyncio.create_task(controller.process_commands())
        
        # 等待初始化完成
        await asyncio.sleep(1)
        
        # 设置全局控制器实例
        from live2d.live2d_main import live2d_controller
        live2d_controller = controller
        
        # 模拟对话流程
        dialogs = [
            "【开心】哈喽~欢迎来找我玩呀！",
            "【调皮】要不要猜猜我今天穿了什么颜色的衣服？",
            "【疑问】你怎么不说话呀？",
            "【惊喜】哇，你终于开口了！",
            "【撒娇】陪我聊聊天嘛~",
            "【开心】好开心能和你聊天！"
        ]
        
        print("\n=== 模拟对话 ===")
        for i, response in enumerate(dialogs):
            print(f"\n对话 {i+1}: {response}")
            
            # 使用 live2d_node 处理
            state = {"response": response}
            result = live2d_node(state)
            print(f"状态: {result}")
            
            await asyncio.sleep(3)  # 等待3秒再进行下一句
        
        # 停止控制器
        controller.running = False
        await asyncio.gather(idle_task, command_task, return_exceptions=True)
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await controller.disconnect()

async def main():
    """主测试函数"""
    parser = argparse.ArgumentParser(description="Live2D 控制器集成测试")
    parser.add_argument("--test", type=str, default="all", 
                        help="测试类型: all/direct/langgraph/dialog")
    args = parser.parse_args()
    
    print("=== Live2D 控制器集成测试 ===")
    
    if args.test == "all" or args.test == "direct":
        await test_direct_commands()
    
    if args.test == "all" or args.test == "langgraph":
        await test_langgraph_node()
    
    if args.test == "all" or args.test == "dialog":
        await test_simulated_dialog()
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n测试被用户中断")
    except Exception as e:
        print(f"测试异常: {e}")
        import traceback
        traceback.print_exc()