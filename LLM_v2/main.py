#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/main.py — V2 完整启动入口
# ============================================================
# 取代 LangGraph，启用单角色 + 三脑 + EDA 架构
# 集成全部组件：Bilibili / TTS / Live2D / Persona + State
#
# 用法：
#   python -m LLM_v2.main                       # 完整启动
#   python -m LLM_v2.main --no-bilibili         # 不连 B 站
#   python -m LLM_v2.main --no-tts              # 不连 TTS
#   python -m LLM_v2.main --no-live2d           # 不连 Live2D
#   python -m LLM_v2.main --test                # 测试模式（5 秒后退出）
# ============================================================

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

# 添加项目根到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.core.llm_adapter import (
    get_streamer_llm, get_will_llm, get_coding_llm,
)
from LLM_v2.io.bilibili_emitter import BilibiliEmitter
from LLM_v2.events import EVENTS

# H-2 控制台：注册脑实例供 webui 读取
from LLM_v2.io.webui import _BRAINS


def _load_llm_env() -> None:
    """从项目 `.env` 注入主模型配置（只认 LLM_API_KEY / LLM_API_URL）。

    项目没有统一的 dotenv 加载器；这两个键只给 LLMAdapter 用，
    单独注入可以避免把 Doubao_API_URL 的改动扩散到视觉/embedding。
    进程环境已存在同名变量时不覆盖（保持"环境变量优先"）。
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in ("LLM_API_KEY", "LLM_API_URL") and key not in os.environ:
                os.environ[key] = value.strip()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("v2.main").warning(
            f"[v2.main] 读取 .env 失败（忽略）: {e}")


_load_llm_env()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("v2.main")


class DigitalBeing:
    """单角色三脑数字人——维尔薇

    集成组件：
        - PersonaRuntime（persona + state 单例）
        - EventBus（24 种事件，异步，错误隔离）
        - 3 个 Brain（WillEngine / Streamer / Operator）
        - 3 个 IO 监听器（TTS / Live2D / Bilibili）
    """

    def __init__(self, config: argparse.Namespace):
        self.config = config
        self.runtime = get_runtime()
        self.will_engine = WillEngineBrain(
            self.runtime,
            decision_mode="hybrid",
            llm=get_will_llm(),               # 意志脑：决策模型
            autonomous=config.autonomous,  # 自主决策开关（默认关；--autonomous 开启）
        )
        self.streamer = StreamerBrain(
            self.runtime,
            llm=get_streamer_llm(),            # 主播脑：数字人实时对话模型
            enable_rag=True,
        )
        self.operator = OperatorBrain(
            self.runtime,
            coding_llm=get_coding_llm(),       # 操作脑：编码循环模型
        )
        # H-2 控制台：注册脑实例
        _BRAINS["will_engine"] = self.will_engine
        _BRAINS["streamer"] = self.streamer
        _BRAINS["operator"] = self.operator
        self._shutdown_event = asyncio.Event()
        self._webui_task = None

        # IO 监听器（懒初始化）
        self.bilibili_emitter: BilibiliEmitter | None = None

        # 注意：TTS / Live2D 已升级为能力域（LLM_v2/domains/tts|live2d），
        # 由 DomainAssembly 装配管理（default_enabled=False 默认不启动）。
        # 不再在 main.py 中直接实例化 TTSListener / Live2DListener。

    async def start(self):
        """启动全部组件"""
        self._print_banner()

        # 1. 启动 EventBus
        await self.runtime.bus.start()

        # 1a. Memory Kernel（轮次摘要 + 长期记忆召回）
        try:
            from persona.memory.service import get_memory_service
            await get_memory_service().start()
            logger.info("[being] Memory Kernel 已启动")
        except Exception as e:
            logger.warning(f"[being] Memory Kernel 启动失败（不影响其他功能）: {e}")

        # 1b. 独立人格内核心跳（自己的节奏，不寄生在任何脑上）
        try:
            self.runtime.start_self_core(30.0)
            logger.info("[being] 人格内核已独立启动（30s 心跳）")
        except Exception as e:
            logger.warning(f"[being] 人格内核心跳启动失败（不影响其他功能）: {e}")

        # 2. 启动 能力域装配（V2 新增：触发自动发现 + 启动激活域）
        try:
            await self.runtime.assembly.start_all()
            domains_status = self.runtime.assembly.worlds_status()
            logger.info(
                f"[being] 域装配完成: {len(domains_status['worlds'])} 个域，"
                f"{sum(1 for w in domains_status['worlds'] if w['started'])} 个已激活"
            )
        except Exception as e:
            logger.warning(f"[being] 域装配启动失败: {e}")

        # 2b. CLI 显式开关覆盖域激活态（--no-tts / --no-live2d / --tts / --live2d）
        await self._apply_io_cli_overrides()

        # 3. 启动 3 脑（WillEngine / Streamer / Operator）
        await self.will_engine.start()
        await self.streamer.start()
        await self.operator.start()

        # 4. 启动 IO 监听器
        await self._start_io()

        # 5. WebUI（可选）
        if not self.config.no_webui:
            try:
                from LLM_v2.io.webui import start_webui
                self._webui_task = asyncio.create_task(
                    start_webui(self.runtime, host="127.0.0.1", port=8081)
                )
                logger.info(f"[being] WebUI 启动中 http://127.0.0.1:8081")
            except Exception as e:
                logger.warning(f"[being] WebUI 启动失败: {e}")

        # 6. 信号处理（优雅退出）
        self._install_signal_handlers()

        # 7. 测试模式：5 秒后退出
        if self.config.test:
            logger.info("[being] TEST 模式：5 秒后退出")
            await asyncio.sleep(5)
            self._shutdown_event.set()
        else:
            logger.info(f"[being] 全部启动完成，等待事件...")

        await self._shutdown_event.wait()
        await self.shutdown()

    async def _apply_io_cli_overrides(self):
        """CLI 参数覆盖域激活态。

        tts/live2d 已域化，默认**挂载不启动**（default_enabled=False）：
        - --tts / --live2d：显式启动对应域
        - --no-tts / --no-live2d：显式停用对应域（覆盖配置/默认）
        """
        asm = self.runtime.assembly
        want = {}
        if getattr(self.config, "tts", False):
            want["tts"] = True
        elif getattr(self.config, "no_tts", False):
            want["tts"] = False
        if getattr(self.config, "live2d", False):
            want["live2d"] = True
        elif getattr(self.config, "no_live2d", False):
            want["live2d"] = False

        for domain_id, enable in want.items():
            try:
                if domain_id not in asm._registry:
                    logger.info(f"[being] CLI 忽略 {domain_id}（域未注册）")
                    continue
                if enable:
                    if domain_id not in asm._active:
                        await asm.start_domain(domain_id)
                        logger.info(f"[being] CLI 启动能力域: {domain_id}")
                else:
                    if domain_id in asm._active:
                        await asm.stop_domain(domain_id)
                        logger.info(f"[being] CLI 停用能力域: {domain_id}")
            except Exception as e:
                logger.warning(f"[being] CLI 覆盖域 {domain_id} 失败: {e}")

    async def _start_io(self):
        """启动 IO 监听器（TTS / Live2D 已域化，由装配层负责；此处只起 B 站发射器）"""
        # Bilibili
        if not self.config.no_bilibili:
            try:
                self.bilibili_emitter = BilibiliEmitter(
                    self.runtime.bus,
                    forward_http=False,  # V2 模式：不走 HTTP 回流
                )
                await self.bilibili_emitter.start()
            except Exception as e:
                logger.warning(f"[being] Bilibili 启动失败: {e}")

    async def shutdown(self):
        """优雅关闭"""
        logger.info("[being] 关闭中...")

        # 关 WebUI
        if self._webui_task and not self._webui_task.done():
            self._webui_task.cancel()
            try:
                await self._webui_task
            except asyncio.CancelledError:
                pass

        # 关 IO
        if self.bilibili_emitter:
            try:
                await self.bilibili_emitter.stop()
            except Exception:
                pass
        # TTS / Live2D 域已由 assembly.stop_all() 统一关闭，无需单独处理

        # 关 Brain
        await self.will_engine.stop()
        await self.streamer.stop()
        await self.operator.stop()

        # 关 Memory Kernel（先停 worker，确保摘要落盘）
        try:
            from persona.memory.service import get_memory_service
            await get_memory_service().stop()
        except Exception as e:
            logger.warning(f"[being] Memory Kernel 停止失败: {e}")

        # 关域装配（V2 新增）
        try:
            await self.runtime.assembly.stop_all()
        except Exception as e:
            logger.warning(f"[being] 域装配停止失败: {e}")

        # 关 Bus
        await self.runtime.bus.stop()

        # 关人格内核心跳（放在最后，确保状态落盘时其他组件已停）
        try:
            await self.runtime.stop_self_core()
        except Exception as e:
            logger.warning(f"[being] 人格内核停止失败: {e}")

        # 打印最终统计
        stats = self.runtime.bus.stats()
        logger.info(f"[being] 最终统计: emitted={stats['emitted']} "
                    f"dispatched={stats['dispatched']} errors={stats['errors']}")
        logger.info("[being] 已关闭")

    def _print_banner(self):
        """打印启动横幅"""
        persona = self.runtime.persona
        state = self.runtime.state
        logger.info("=" * 60)
        logger.info("V2 启动：单角色三脑数字人（EDA 架构）")
        logger.info(f"  人格：{persona.name}")
        logger.info(f"  爱好：{[i.name for i in persona.interests[:3]]}")
        logger.info(f"  状态：{state.pad.dominant_quadrant()} "
                    f"(P={state.pad.P:.2f}, A={state.pad.A:.2f}, D={state.pad.D:.2f})")
        logger.info(f"  能量：{state.energy}/100  专注：{state.focus}/100  "
                    f"社交需求：{state.social_need}/100  好奇：{state.curiosity}/100")
        logger.info(f"  Brain: WillEngine (30s, autonomous={'on' if self.config.autonomous else 'off'}) "
                    f"+ Streamer (2s) + Operator (3s)")
        logger.info(f"  IO: Bilibili={'on' if not self.config.no_bilibili else 'off'}  "
                    f"WebUI={'on' if not self.config.no_webui else 'off'}")
        logger.info(f"  Capability: TTS={'on' if getattr(self.config,'tts',False) else 'mounted'}  "
                    f"Live2D={'on' if getattr(self.config,'live2d',False) else 'mounted'}")
        logger.info("=" * 60)

    def _install_signal_handlers(self):
        """Ctrl+C 优雅关闭"""
        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("[being] 收到 SIGINT，触发关闭")
            self._shutdown_event.set()

        try:
            loop.add_signal_handler(signal.SIGINT, _signal_handler)
        except NotImplementedError:
            # Windows 不支持
            pass

    # ---------- 测试钩子 ----------

    async def emit_test_event(self, event_type: str, payload=None):
        """测试用：发射一个事件"""
        await self.runtime.bus.emit(event_type, payload)


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    p = argparse.ArgumentParser(description="LLM_vtuber V2 数字人启动入口")
    p.add_argument("--no-bilibili", action="store_true", help="不连接 Bilibili")
    p.add_argument("--autonomous", action="store_true",
                   help="开启思维脑自主决策（空闲时自行想干啥；默认关闭）")
    p.add_argument("--tts", action="store_true", help="显式启动 TTS 能力域（默认挂载不启动）")
    p.add_argument("--no-tts", action="store_true", help="强制停用 TTS 能力域")
    p.add_argument("--live2d", action="store_true", help="显式启动 Live2D 能力域（默认挂载不启动）")
    p.add_argument("--no-live2d", action="store_true", help="强制停用 Live2D 能力域")
    p.add_argument("--no-webui", action="store_true", help="不启动 WebUI")
    p.add_argument("--no-rerank", action="store_true",
                   help="关闭知识检索 rerank（等价 rerank_enabled=false）")
    p.add_argument("--test", action="store_true", help="测试模式（5 秒后退出）")
    p.add_argument("--emit", action="append", default=[],
                   help="启动后发射测试事件（可多次），格式: EVENT_TYPE|payload_json")
    return p.parse_args()


async def main():
    args = parse_args()
    if args.no_rerank:
        # 思维脑知识件在构造时读取该环境变量
        os.environ["RAG_RERANK_ENABLED"] = "false"
    being = DigitalBeing(args)

    # 启动任务
    start_task = asyncio.create_task(being.start())

    # 测试事件发射
    if args.emit:
        await asyncio.sleep(2)  # 等系统启动
        for spec in args.emit:
            if "|" in spec:
                et, payload_str = spec.split("|", 1)
                try:
                    import json
                    payload = json.loads(payload_str)
                except Exception:
                    payload = {"text": payload_str}
            else:
                et, payload = spec, {}
            logger.info(f"[main] 测试事件: {et} {payload}")
            await being.emit_test_event(et, payload)

    await start_task


if __name__ == "__main__":
    asyncio.run(main())
