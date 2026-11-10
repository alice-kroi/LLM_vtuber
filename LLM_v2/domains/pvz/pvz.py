# ============================================================
# LLM_v2/domains/pvz/pvz.py — pvz 域类（V2 原生）
# ============================================================
# 替换 V1 LLM/pvz/pvz_world.py 的 PvzWorld
# ============================================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Transceiver,
    CapabilityType,
)
from LLM_v2.domains.pvz.pvz_tools import PVZ_TOOL_NAMES, PVZ_TOOLS_SCHEMA

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


class PvzDomain(SubprocessCapabilityDomain, Transceiver):
    """植物大战僵尸语义世界域（V2 子进程域，H-2 Transceiver 类型）"""

    id = "pvz"
    label = "植物大战僵尸语义世界"
    prefix = "pvz_"
    default_enabled = False     # 默认关闭（依赖 PvZ 游戏进程）
    type = CapabilityType.TRANSCEIVER

    call_timeout = 30.0
    tool_timeouts = {
        "pvz_observe": 15.0,
        "pvz_glance": 30.0,
        "pvz_do": 10.0,
        "pvz_queue": 10.0,
        "pvz_arm": 10.0,
        "pvz_stop": 5.0,
    }

    def inbound_event_types(self):
        """PvZ 能力声明：感知类工具（pvz_observe）触发 PERCEPTION_UPDATED"""
        return ["perception_updated"]

    def outbound_event_types(self):
        return []

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "pvz_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(PVZ_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in PVZ_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _PVZ_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：PvZ 进程是否在跑（可选，非阻塞）"""
        try:
            import subprocess
            # 简单 tasklist 探测（如失败不阻塞）
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq PlantsVsZombies.exe"],
                capture_output=True, text=True, timeout=5,
            )
            if "PlantsVsZombies.exe" in out.stdout:
                return None
            # 未运行也不阻止启动（用户可能想先看内存布局）
            return None
        except Exception:
            return None

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_pvz_domain() -> PvzDomain:
    return PvzDomain()


__all__ = ['PvzDomain', 'build_pvz_domain']