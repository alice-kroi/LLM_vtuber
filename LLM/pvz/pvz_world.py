#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PvzWorld：植物大战僵尸语义能力域（纯 Python 子进程载体）。"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.pvz.pvz_tools import PVZ_TOOL_NAMES, PVZ_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

_PVZ_PROMPT = """可用工具：
- pvz_observe: 读取语义局面（阶段/太阳/卡片/棋盘植物/每排僵尸/收集目标）
- pvz_do: 提交有序语义步骤（选种子/种植/铲除/收集/menu 等），立即返回接纳回执
- pvz_queue: 查看运行/等待/停靠任务与触发、最近终态
- pvz_arm: 武装触发（条件成立自动执行），如樱桃炸弹等僵尸排队再炸
- pvz_stop: 撤回任务/触发，或全部停止
- pvz_glance: 截取一帧用于诊断（仍走语义过滤）

执行原则：
1. 决策只看 pvz_observe 的真实语义局面，不猜、不用截图猜隐藏体。
2. 植物与排/列用人类语言（如 第3排第6列 种 向日葵）；不要用内部 id。
3. 太阳会自动收集，无需手动；金币等需显式 pvz_do collect。
4. 一次只提交一个 pvz_do 任务队列；等待其对局进展用 pvz_queue 查终态。
5. 条件型（等冷却/等足够的阳光/等僵尸到位）用 pvz_arm 而非轮询。
"""


class PvzWorld(SubprocessSkillWorld):
    id = "pvz"
    label = "植物大战僵尸语义世界"
    prefix = "pvz_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "pvz_engine.py")

    def launch_args(self):
        # python 由 SubprocessSkillWorld.python_interp 提供；这里给到入口脚本
        return [self._engine_path()]

    def allowed_names(self):
        return list(PVZ_TOOL_NAMES)

    def tools(self):
        return [s for s in PVZ_TOOLS_SCHEMA if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _PVZ_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info(f"[PvzWorld] pvz 域已启动并注册代理工具 {self.allowed_names()}")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[PvzWorld] pvz 域已停止并注销代理工具")


# 默认启用状态由 skill_worlds.json / 环境变量决定；这里留一个便于装配层引用的工厂
def build_pvz_world() -> PvzWorld:
    return PvzWorld()