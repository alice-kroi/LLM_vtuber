#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 模型方向测试脚本

测试模型在各个方向的动作
"""

import asyncio
import sys
import os

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d_main import Live2DMain

async def test_directions():
    """测试各个方向的动作"""
    print("=== Live2D 模型方向测试 ===")
    
    # 创建控制器实例
    controller = Live2DMain()
    
    try:
        # 连接服务器
        if not await controller.connect():
            print("连接服务器失败")
            return
        
        # 登录认证
        if not await controller.login():
            print("登录认证失败")
            await controller.disconnect()
            return
        
        print("登录成功")
        
        # 测试方向列表
        directions = [
            "center",
            "up",
            "down",
            "left",
            "right",
            "upleft",
            "upright",
            "downleft",
            "downright"
        ]
        
        # 测试每个方向
        for direction in directions:
            print(f"\n测试方向: {direction}")
            # 设置方向
            await controller.set_direction(direction)
            # 等待3秒，观察动作
            await asyncio.sleep(3)
        
        # 最后回到中心位置
        print("\n回到中心位置")
        await controller.set_direction("center")
        await asyncio.sleep(2)
        
    except KeyboardInterrupt:
        print("\n测试被用户中断")
    finally:
        # 断开连接
        await controller.disconnect()
        print("测试完成")

if __name__ == "__main__":
    asyncio.run(test_directions())
