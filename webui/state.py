#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web UI 共享状态模块

解决 Python 模块导入机制导致的状态不同步问题：
- 当 main.py 作为 __main__ 运行时，其全局变量在 __main__ 命名空间中
- 当 webui/app.py 通过 import main 访问时，获取的是 main 模块的独立副本
- 通过此模块作为共享状态容器，两个命名空间都访问同一份数据
"""


class AppState:
    """应用运行时状态容器"""

    def __init__(self):
        # 命令行参数
        self.args = None

        # 管理器实例
        self.live2d_manager = None
        self.langgraph_manager = None

        # 弹幕进程
        self._bili_process = None

        # 消息队列
        self._message_queue = None
        self._queue_worker_task = None

        # 运行状态
        self.startup_complete = False


# 全局唯一实例
state = AppState()
