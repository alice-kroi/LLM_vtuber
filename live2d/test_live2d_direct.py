#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 直接测试程序
直接连接到 VTube Studio 进行测试
"""

import asyncio
import sys
import os

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d.live2d_main import Live2DMain

async def test_visual_focus(controller):
    """测试视觉焦点"""
    print("=== 测试视觉焦点 ===")
    
    directions = ["center", "up", "down", "left", "right", "upleft", "upright", "downleft", "downright"]
    
    for direction in directions:
        print(f"\n移动到 {direction}...")
        await controller.move_to_direction(direction, duration=1.5)  # 增加移动时间
        await asyncio.sleep(2.0)  # 等待动作完成

async def test_actions(controller):
    """测试动作"""
    print("\n=== 测试动作 ===")
    
    print("\n张开嘴巴...")
    await controller.set_mouth_state(True)
    await asyncio.sleep(1)
    
    print("\n关闭嘴巴...")
    await controller.set_mouth_state(False)
    await asyncio.sleep(1)

async def test_combined(controller):
    """测试组合指令"""
    print("\n=== 测试组合指令 ===")
    
    print("\n向上看并张开嘴巴...")
    await controller.move_to_direction("up", duration=1.5)
    await asyncio.sleep(2.0)
    await controller.set_mouth_state(True)
    await asyncio.sleep(1.5)
    
    print("\n回到中心并关闭嘴巴...")
    await controller.move_to_direction("center", duration=1.5)
    await asyncio.sleep(2.0)
    await controller.set_mouth_state(False)
    await asyncio.sleep(1.5)

async def test_conversation(controller):
    """测试对话场景"""
    print("\n=== 测试对话场景 ===")
    
    dialogs = [
        {"direction": "center", "mouth_open": True, "text": "【开心】哈喽~欢迎来找我玩呀！"},
        {"direction": "up", "mouth_open": False, "text": "【疑问】你今天有空吗？"},
        {"direction": "right", "mouth_open": True, "text": "【调皮】要不要一起玩游戏？"},
        {"direction": "center", "mouth_open": False, "text": "【期待】我等着你的回答哦~"},
    ]
    
    for dialog in dialogs:
        print(f"\n{dialog['text']}")
        await controller.move_to_direction(dialog['direction'], duration=1.5)
        await asyncio.sleep(2.0)
        await controller.set_mouth_state(dialog['mouth_open'])
        await asyncio.sleep(2.5)

async def main():
    """主测试函数"""
    print("=== Live2D 直接测试程序 ===")
    
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
        
        # 设置初始状态
        await controller.set_mouth_state(False)
        
        # 启动常态运动（在后台）
        controller.running = True
        idle_task = asyncio.create_task(controller.idle_movement())
        
        # 等待初始化完成
        await asyncio.sleep(1)
        
        # 运行测试
        await test_visual_focus(controller)
        await test_actions(controller)
        await test_combined(controller)
        await test_conversation(controller)
        
        # 停止控制器
        controller.running = False
        await idle_task
        
    except KeyboardInterrupt:
        print("\n测试被用户中断")
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await controller.disconnect()
        print("\n=== 测试完成 ===")

if __name__ == "__main__":
    asyncio.run(main())