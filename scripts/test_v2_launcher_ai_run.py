"""AI 发现 launcher 能力（mumu/alas/maa）并通过事件驱动实际运行

链路：EventBus → OperatorBrain → DomainAssembly.call_tool → launcher 子进程域
验证点：
  1. AI 能"发现"能力：assembly 聚合出 launcher 域 + 6 个工具 schema
  2. AI 能"运行"能力：emit DO_TASK → operator 执行 → OPERATOR_DONE 回流
  3. 依次运行：launcher_mumu → launcher_alas → launcher_maa → launcher_status
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.events import EVENTS


def _reset_runtime():
    PersonaRuntime._instance = None


def ai_discover(runtime) -> dict:
    """模拟 AI 发现能力：列出域 + 工具 schema"""
    print("\n" + "=" * 70)
    print("[AI 发现] 扫描能力域…")
    print("=" * 70)
    asm = runtime.assembly
    status = asm.worlds_status()
    launcher_world = next((w for w in status["worlds"] if w["id"] == "launcher"), None)
    print(f"[发现] launcher 域: {launcher_world}")
    names = asm.active_tool_names()
    launcher_tools = [n for n in names if n.startswith("launcher_")]
    print(f"[发现] 当前激活工具总数={len(names)}, launcher 域工具={launcher_tools}")
    schemas = asm.active_tool_schemas()
    launcher_schemas = [s for s in schemas if s["function"]["name"].startswith("launcher_")]
    for s in launcher_schemas:
        fn = s["function"]
        print(f"  · {fn['name']}: {fn['description'][:60]}…")
    return launcher_world


async def ai_run(runtime, operator) -> list:
    """模拟 AI 运行：emit DO_TASK → 事件驱动执行"""
    print("\n" + "=" * 70)
    print("[AI 运行] 通过 EventBus 派发任务…")
    print("=" * 70)
    results = []

    async def on_done(payload):
        if isinstance(payload, dict):
            results.append(payload)

    runtime.bus.on(EVENTS.OPERATOR_DONE, on_done)

    def emit(tool, args, desc):
        print(f"\n>> emit DO_TASK: {tool}({args}) — {desc}")
        return runtime.bus.emit(EVENTS.DO_TASK, {
            "tool": tool, "args": args, "description": desc,
            "source": "ai_test",
        })

    # 1) 先看现状（不启动任何东西）
    await emit("launcher_status", {}, "查询当前 MuMu/ALAS/MAA 窗口状态")
    await asyncio.sleep(2.0)

    # 2) 一键编排：启动两个 MuMu 实例 [0,1] + ALAS + MAA，并在主界面点击"启动"按钮
    #    click_start=True → ALAS/MAA 界面上的挂机脚本真正开始运行
    await emit("launcher_start_all",
               {"instances": [0, 1], "click_start": True},
               "一键启动 MuMu 实例0+1 + ALAS + MAA，并点击各自主界面启动按钮")
    await asyncio.sleep(90.0)  # start_all 内部含 15s mumu 等待 + 按钮点击超时

    # 3) 再次确认窗口
    await emit("launcher_status", {}, "确认两个实例 + 脚本启动结果")
    await asyncio.sleep(3.0)

    return results


def main():
    print("=" * 70)
    print("  AI 发现并运行 launcher 能力（mumu / alas / maa）")
    print("=" * 70)
    _reset_runtime()
    runtime = get_runtime()

    # AI 发现能力
    launcher_world = ai_discover(runtime)
    if not launcher_world:
        print("[FAIL] 未发现 launcher 域")
        sys.exit(1)

    async def _main():
        asm = runtime.assembly
        # 显式激活 launcher 域（default_enabled=False，需手动启用）
        asm.activate("launcher")
        await asm.start_domain("launcher")
        print(f"\n[激活] launcher 域已激活 + 生命周期 start 完成")

        await runtime.bus.start()
        operator = OperatorBrain(runtime)
        await operator.start()

        results = await ai_run(runtime, operator)

        # 汇总
        print("\n" + "=" * 70)
        print("[汇总] OPERATOR_DONE 回流结果")
        print("=" * 70)
        for i, r in enumerate(results):
            task = r.get("task", {})
            tool = task.get("tool", "?")
            result = r.get("result", {})
            ok = result.get("success")
            print(f"\n[{i+1}] {tool} → success={ok}")
            inner = result.get("result", {})
            if not ok and result.get("error"):
                print(f"    error: {result['error'][:200]}")
            elif isinstance(inner, dict):
                for k, v in list(inner.items())[:6]:
                    print(f"    {k}: {str(v)[:120]}")

        await operator.stop()
        await asm.stop_domain("launcher")
        await runtime.bus.stop()
        print("\n[完成] AI 发现 + 运行 launcher 能力流程结束")

    asyncio.run(_main())


if __name__ == "__main__":
    main()
