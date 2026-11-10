"""一键启动：MuMu 实例[0,1] + ALAS + MAA，并点击各自主界面启动按钮"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


async def main():
    print("=" * 70)
    print("  启动 MuMu[0,1] + ALAS + MAA（含点击启动按钮）")
    print("=" * 70)
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly

    # 激活 launcher 域
    asm.activate("launcher")
    await asm.start_domain("launcher")
    print("[1] launcher 域已激活")

    # 一键编排：两实例 + 点击启动
    print("[2] 调用 launcher_start_all(instances=[0,1], click_start=True) ...")
    result = await asm.call_tool(
        "launcher_start_all",
        {"instances": [0, 1], "click_start": True},
        timeout=260.0,
    )
    print("\n[3] 结果:")
    if result.get("success"):
        inner = result.get("result", {})
        # 打印各环节
        for key in ("mumu", "alas_launch", "alas_start", "alas_start_web",
                    "alas_start_coord", "maa_launch", "maa_start", "maa_start_coord",
                    "final_windows"):
            if key in inner:
                print(f"  · {key}: {inner[key]}")
    else:
        print(f"  [FAIL] {result.get('error')}")

    # 最终窗口确认
    print("\n[4] 最终确认:")
    status = await asm.call_tool("launcher_status", {}, timeout=10.0)
    print(f"  {status.get('result')}")

    await asm.stop_domain("launcher")
    print("\n[完成]")


asyncio.run(main())
