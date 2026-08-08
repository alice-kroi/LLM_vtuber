#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 enable_live2d 参数传递
"""

import sys
import os

# 添加项目根目录到sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_enable_live2d():
    """测试 enable_live2d 参数传递"""
    
    # 模拟导入
    try:
        from live2d.live2d_controller_manager import (
            Live2DControllerManager,
            Live2DConfig,
            ActionGenerator,
            Direction
        )
        live2d_available = True
        print(f"✓ Live2D模块导入成功，live2d_available={live2d_available}")
    except ImportError as e:
        live2d_available = False
        print(f"✗ Live2D模块导入失败: {e}")
    
    # 测试 enable_live2d 的值
    args_live2d = True  # 假设命令行参数是 --live2d
    
    enable_live2d = args_live2d and live2d_available
    print(f"\n参数计算结果:")
    print(f"  args.live2d = {args_live2d}")
    print(f"  live2d_available = {live2d_available}")
    print(f"  enable_live2d = {enable_live2d}")
    
    # 测试状态传递
    state = {}
    state["enable_live2d"] = enable_live2d
    print(f"\n状态传递:")
    print(f"  state['enable_live2d'] = {state['enable_live2d']}")
    
    # 模拟LLM节点读取
    enable_live2d_from_state = state.get("enable_live2d", False)
    print(f"\nLLM节点读取:")
    print(f"  enable_live2d = {enable_live2d_from_state}")
    print(f"  是否启用Live2D: {'是' if enable_live2d_from_state else '否'}")

if __name__ == "__main__":
    test_enable_live2d()
