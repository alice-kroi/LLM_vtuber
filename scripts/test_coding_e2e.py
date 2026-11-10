#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 端到端测试：USER_MESSAGE → 意志脑路由 → 操作脑 CodingLoop → TASK_DONE/FAILED

特点：**所有输出实时可见**（终端 + 日志文件同时写，逐行 flush），
      并且每 20 秒打印心跳，便于确认程序没有卡死。

不启动 B 站 / TTS / Live2D / WebUI，只验证"事件链 + 编码闭环"。

用法：
    python -u scripts/test_coding_e2e.py
    python -u scripts/test_coding_e2e.py "你的指令" 480
"""

import asyncio
import io
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

START_TS = time.time()
LOG_PATH = Path("test_output") / f"coding_e2e_{time.strftime('%Y%m%d_%H%M%S')}.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class Tee(io.TextIOBase):
    """同时写终端与日志文件，每次写入立即 flush（避免管道缓冲看不到进展）。"""

    def __init__(self, path: Path):
        self._term = sys.__stdout__
        self._file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, s):
        try:
            self._term.write(s)
            self._term.flush()
        except Exception:
            pass
        try:
            self._file.write(s)
            self._file.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self._term.flush()
        except Exception:
            pass
        try:
            self._file.flush()
        except Exception:
            pass


sys.stdout = Tee(LOG_PATH)
sys.stderr = sys.stdout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)


def say(msg: str):
    print(f"[{time.strftime('%H:%M:%S')} +{time.time() - START_TS:6.1f}s] {msg}", flush=True)


GOAL = sys.argv[1] if len(sys.argv) > 1 else (
    "用 PyTorch 写一个小型 CNN 模型定义（只做前向传播验证，不要训练），"
    "然后用随机张量跑一次 forward，打印输出 shape，确认能正常运行"
)
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 480.0


async def main():
    from LLM_v2.core.persona_runtime import get_runtime
    from LLM_v2.brains.will_engine import WillEngineBrain
    from LLM_v2.brains.operator import OperatorBrain
    from LLM_v2.core.llm_adapter import get_will_llm, get_coding_llm
    from LLM_v2.events import EVENTS

    say("=" * 72)
    say("指令: " + GOAL)
    say("日志文件: " + str(LOG_PATH.resolve()))
    say("=" * 72)

    runtime = get_runtime()
    await runtime.bus.start()
    say("EventBus 已启动")

    for did in ("desktop", "coder"):
        try:
            if did not in runtime.assembly._active:
                await runtime.assembly.start_domain(did)
            say(f"域 {did} 已激活")
        except Exception as e:  # noqa: BLE001
            say(f"[warn] 域 {did} 启动失败: {e}")

    active = runtime.assembly.active_tool_names()
    say(f"已激活工具 {len(active)} 个；coder: {[n for n in active if n.startswith('code_')]}")

    will = WillEngineBrain(runtime, decision_mode="hybrid",
                           llm=get_will_llm(), autonomous=False)
    op = OperatorBrain(runtime, coding_llm=get_coding_llm())
    await will.start()
    await op.start()
    say("意志脑 / 操作脑已启动")

    done = asyncio.Event()
    box = {}

    async def on_done(payload):
        box["result"] = payload
        done.set()

    async def on_progress(payload):
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        p = payload.get("progress") or {}
        say(f"[进度 {p.get('percent', 0):3d}%] {p.get('stage', '')}: {p.get('detail', '')[:100]}")

    async def on_report(payload):
        if isinstance(payload, dict) and payload.get("report"):
            say("[播报] " + str(payload["report"])[:200])

    runtime.bus.on(EVENTS.TASK_DONE, on_done)
    runtime.bus.on(EVENTS.TASK_FAILED, on_done)
    runtime.bus.on(EVENTS.OPERATOR_PROGRESS, on_progress)
    runtime.bus.on(EVENTS.PROGRESS_REPORT, on_report)

    async def heartbeat():
        n = 0
        while not done.is_set():
            await asyncio.sleep(20)
            if done.is_set():
                break
            n += 1
            say(f"…仍在执行（心跳 #{n}，已等待 {time.time() - START_TS:.0f}s）")

    hb = asyncio.create_task(heartbeat())

    say("发送 USER_MESSAGE → 等待链路…")
    await runtime.bus.emit(EVENTS.USER_MESSAGE, {
        "text": GOAL, "source": "webui", "user": "tester",
    })

    try:
        await asyncio.wait_for(done.wait(), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        say(f"[FAIL] {TIMEOUT:.0f}s 内未收到 TASK_DONE/TASK_FAILED")

    hb.cancel()

    say("=" * 72)
    say("总耗时: %.1fs" % (time.time() - START_TS))
    res = (box.get("result") or {}).get("result") or {}
    say("success      = %s" % res.get("success"))
    say("rounds       = %s" % res.get("rounds"))
    say("tool_calls   = %s" % res.get("tool_calls"))
    say("files_changed= %s" % res.get("files_changed"))
    say("verified     = %s" % res.get("verified"))
    say("error        = %s" % res.get("error"))
    say("last_verify  = %s" % json.dumps(res.get("last_verify"), ensure_ascii=False)[:600])
    say("=" * 72)

    await will.stop()
    await op.stop()
    try:
        await runtime.assembly.stop_all()
    except Exception:
        pass
    await runtime.bus.stop()
    say("已退出")


if __name__ == "__main__":
    asyncio.run(main())
